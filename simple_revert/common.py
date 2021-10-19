# Common constants and functions for reverting scripts.
import getpass
import base64
import logging
import re
import requests

try:
    from lxml import etree
except ImportError:
    try:
        import xml.etree.cElementTree as etree
    except ImportError:
        import xml.etree.ElementTree as etree
try:
    input = raw_input
except NameError:
    pass

API_ENDPOINT = 'https://api.openstreetmap.org/api/0.6/'


class HTTPError(Exception):
    def __init__(self, code, message):
        self.code = code
        self.message = message

    def __str__(self):
        return 'HTTPError({}, {})'.format(self.code, self.message)


class RevertError(Exception):
    def __init__(self, msg):
        self.message = msg

    def __str__(self):
        return 'RevertError({})'.format(self.message)


def api_request(endpoint, method='GET', sysexit_message=None,
                raw_result=False, headers=None, **kwargs):
    if not headers:
        headers = {}
    headers['Content-Type'] = 'application/xml'
    try:
        resp = requests.request(method, API_ENDPOINT + endpoint, headers=headers, **kwargs)
        resp.encoding = 'utf-8'
        if resp.status_code != 200:
            raise HTTPError(resp.status_code, resp.text)
        if resp.content and not raw_result:
            return etree.fromstring(resp.content)
    except Exception as e:
        if sysexit_message is not None:
            raise RevertError(': '.join((sysexit_message, str(e))))
        raise e
    return resp.text


def read_auth():
    """Read login and password from keyboard, and prepare an basic auth header."""
    ok = False
    while not ok:
        login = input('OSM Login: ')
        auth_header = 'Basic {0}'.format(base64.b64encode('{0}:{1}'.format(
            login, getpass.getpass('OSM Password: ')).encode('utf-8')).decode('utf-8'))
        try:
            result = api_request('user/details', headers={'Authorization': auth_header})
            ok = len(result) > 0
        except Exception as e:
            logging.error(e)
        if not ok:
            logging.warning('You must have mistyped. Please try again.')
    return auth_header


def obj_to_dict(obj):
    """Converts XML object to an easy to use dict."""
    if obj is None:
        return None
    res = {}
    res['type'] = obj.tag
    res['id'] = obj.get('id')
    res['version'] = int(obj.get('version'))
    res['deleted'] = obj.get('visible') == 'false'
    if obj.tag == 'node' and 'lon' in obj.keys() and 'lat' in obj.keys():
        res['coords'] = (obj.get('lon'), obj.get('lat'))
    res['tags'] = {tag.get('k'): tag.get('v') for tag in obj.findall('tag')}
    if obj.tag == 'way':
        res['refs'] = [x.get('ref') for x in obj.findall('nd')]
    elif obj.tag == 'relation':
        res['refs'] = [(x.get('type'), x.get('ref'), x.get('role')) for x in obj.findall('member')]
    return res


def dict_to_obj(obj):
    """Converts object dict back to an XML element."""
    if obj is None:
        return None
    res = etree.Element(obj['type'], {'id': str(obj['id']), 'version': str(obj['version'])})
    res.set('visible', 'false' if obj['deleted'] else 'true')
    if 'coords' in obj:
        res.set('lon', obj['coords'][0])
        res.set('lat', obj['coords'][1])
    if 'tags' in obj:
        for k, v in obj['tags'].items():
            res.append(etree.Element('tag', {'k': k, 'v': v}))
    if not obj['deleted']:
        if obj['type'] == 'way':
            for nd in obj['refs']:
                res.append(etree.Element('nd', {'ref': nd}))
        elif obj['type'] == 'relation':
            for member in obj['refs']:
                res.append(etree.Element('member', {'type': member[0],
                                                    'ref': member[1],
                                                    'role': member[2]}))
    return res


def changes_to_osc(changes, changeset_id=None):
    # Set explicit actions for each changed object
    for c in changes:
        if 'version' not in c or c['version'] <= 0:
            c['action'] = 'create'
        elif 'deleted' in c and c['deleted']:
            c['action'] = 'delete'
        else:
            c['action'] = 'modify'

    # Sort changes, so created nodes are first, and deleted are last
    def change_as_key(ch):
        act = ['create', 'modify', 'delete'].index(ch['action'])
        typ = ['node', 'way', 'relation'].index(ch['type'])
        if act == 2:
            typ = 2 - typ
        return '{0}{1}{2}'.format(act, typ, ch['id'])

    changes.sort(key=change_as_key)

    osc = etree.Element('osmChange', {'version': '0.6'})
    for c in changes:
        act = etree.SubElement(osc, c['action'])
        el = dict_to_obj(c)
        if changeset_id:
            el.set('changeset', str(changeset_id))
        act.append(el)

    try:
        return etree.tostring(osc, pretty_print=True, encoding='utf-8', xml_declaration=True)
    except TypeError:
        # xml.etree.ElementTree does not support pretty printing
        return etree.tostring(osc, encoding='utf-8')


def changeset_xml(changeset_tags):
    create_xml = etree.Element('osm')
    ch = etree.SubElement(create_xml, 'changeset')
    for k, v in changeset_tags.items():
        ch.append(etree.Element('tag', {'k': k, 'v': v}))
    return etree.tostring(create_xml)


def upload_changes(changes, changeset_tags):
    """Uploads a list of changes as tuples (action, obj_dict)."""
    if not changes:
        logging.info('No changes to upload.')
        return False

    # Now we need the OSM credentials
    auth_header = read_auth()
    headers = {'Authorization': auth_header}

    try:
        changeset_id = int(api_request(
            'changeset/create', 'PUT', raw_result=True,
            data=changeset_xml(changeset_tags), headers=headers,
        ))
        logging.info('Writing to changeset %s', changeset_id)
    except Exception as e:
        logging.exception(e)
        logging.error('Failed to create changeset: %s', e)
        return False
    osc = changes_to_osc(changes, changeset_id)

    ok = True
    try:
        api_request(
            'changeset/{}/upload'.format(changeset_id), 'POST',
            data=osc, headers=headers
        )
    except HTTPError as e:
        logging.error('Server rejected the changeset with code %s: %s', e.code, e.message)
        if e.code == 412:
            # Find the culprit for a failed precondition
            m = re.search(r'Node (\d+) is still used by (way|relation)s ([0-9,]+)', e.message)
            if m:
                # Find changeset for the first way or relation that started using that node
                pass
            else:
                m = re.search(r'(Way|The relation) (\d+) is .+ relations? ([0-9,]+)', e.message)
                if m:
                    # Find changeset for the first relation that started using that way or relation
                    pass
                else:
                    m = re.search(r'Way (\d+) requires .+ id in \(([0-9,]+\)', e.message)
                    if m:
                        # Find changeset that deleted at least the first node in the list
                        pass
                    else:
                        m = re.search(r'Relation with id (\d+) .+ due to (\w+) with id (\d+)',
                                      e.message)
                        if m:
                            # Find changeset that added member to that relation
                            pass
    except Exception as e:
        ok = False
        logging.error('Failed to upload changetset contents: %s', e)
        # Not returning, since we need to close the changeset

    try:
        api_request('changeset/{}/close'.format(changeset_id), 'PUT', headers=headers)
    except Exception as e:
        logging.warning(
            'Failed to close changeset (it will close automatically in an hour): %s', e)
    return ok
