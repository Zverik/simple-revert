# Common constants and functions for reverting scripts.
import urllib2, getpass, base64, re

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

API_ENDPOINT = 'https://api.openstreetmap.org'
# API_ENDPOINT = 'http://master.apis.dev.openstreetmap.org'


# Copied from http://stackoverflow.com/a/3884771/1297601
class MethodRequest(urllib2.Request):
    """A subclass to override Request method and content type."""
    GET = 'GET'
    POST = 'POST'
    PUT = 'PUT'
    DELETE = 'DELETE'

    def __init__(self, url, data=None, headers={},
                 origin_req_host=None, unverifiable=False, method=None):
        headers['Content-Type'] = 'application/xml'
        urllib2.Request.__init__(self, url, data, headers, origin_req_host, unverifiable)
        self.method = method

    def get_method(self):
        if self.method:
            return self.method
        return urllib2.Request.get_method(self)


def read_auth():
    """Read login and password from keyboard, and prepare an basic auth header."""
    ok = False
    while not ok:
        login = input('OSM Login: ')
        auth_header = 'Basic {0}'.format(base64.b64encode('{0}:{1}'.format(login, getpass.getpass('OSM Password: '))))
        try:
            request = urllib2.Request(API_ENDPOINT + '/api/0.6/user/details')
            request.add_header('Authorization', auth_header)
            result = urllib2.urlopen(request)
            ok = 'account_created' in result.read()
        except Exception as e:
            print(e)
        if not ok:
            print('You must have mistyped. Please try again.')
    return auth_header


def obj_to_dict(obj):
    """Converts XML object to an easy to use dict."""
    if obj is None:
        return None
    res = {}
    res['type'] = obj.tag
    res['id'] = int(obj.get('id'))
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
        for k, v in obj['tags'].iteritems():
            res.append(etree.Element('tag', {'k': k, 'v': v}))
    if not obj['deleted']:
        if obj['type'] == 'way':
            for nd in obj['refs']:
                res.append(etree.Element('nd', {'ref': nd}))
        elif obj['type'] == 'relation':
            for member in obj['refs']:
                res.append(etree.Element('member', {'type': member[0], 'ref': member[1], 'role': member[2]}))
    return res


class HTTPError:
    def __init__(self, e):
        self.code = e.code
        self.message = e.read()


class RevertError:
    def __init__(self, msg):
        self.message = msg


def api_download(method, throw=None, sysexit_message=None):
    """Downloads an XML response from the OSM API. Returns either an Element, or a tuple of (code, message)."""
    try:
        try:
            response = urllib2.urlopen('{0}/api/0.6/{1}'.format(API_ENDPOINT, method))
            return etree.parse(response).getroot()
        except urllib2.HTTPError as e:
            if throw is not None and e.code in throw:
                raise HTTPError(e)
            else:
                raise e
    except Exception as e:
        if sysexit_message is not None:
            raise RevertError(': '.join((sysexit_message, str(e))))
        raise e


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
    for k, v in changeset_tags.iteritems():
        ch.append(etree.Element('tag', {'k': k, 'v': v.decode('utf-8')}))
    return etree.tostring(create_xml)


def upload_changes(changes, changeset_tags):
    """Uploads a list of changes as tuples (action, obj_dict)."""
    if not changes:
        print('No changes to upload.')
        return False

    # Now we need the OSM credentials
    auth_header = read_auth()
    opener = urllib2.build_opener()
    opener.addheaders = [('Authorization', auth_header)]

    request = MethodRequest(API_ENDPOINT + '/api/0.6/changeset/create', changeset_xml(changeset_tags), method=MethodRequest.PUT)
    try:
        changeset_id = int(opener.open(request).read())
        print('Writing to changeset {0}'.format(changeset_id))
    except Exception as e:
        print('Failed to create changeset: {0}'.format(e))
        return False
    osc = changes_to_osc(changes, changeset_id)

    ok = True
    request = MethodRequest('{0}/api/0.6/changeset/{1}/upload'.format(API_ENDPOINT, changeset_id), osc, method=MethodRequest.POST)
    try:
        opener.open(request)
    except urllib2.HTTPError as e:
        message = e.read()
        print('Server rejected the changeset with code {0}: {1}'.format(e.code, message))
        if e.code == 412:
            # Find the culprit for a failed precondition
            m = re.search(r'Node (\d+) is still used by (way|relation)s ([0-9,]+)', message)
            if m:
                # Find changeset for the first way or relation that started using that node
                pass
            else:
                m = re.search(r'(Way|The relation) (\d+) is .+ relations? ([0-9,]+)', message)
                if m:
                    # Find changeset for the first relation that started using that way or relation
                    pass
                else:
                    m = re.search(r'Way (\d+) requires .+ id in \(([0-9,]+\)', message)
                    if m:
                        # Find changeset that deleted at least the first node in the list
                        pass
                    else:
                        m = re.search(r'Relation with id (\d+) .+ due to (\w+) with id (\d+)', message)
                        if m:
                            # Find changeset that added member to that relation
                            pass
    except Exception as e:
        ok = False
        print('Failed to upload changetset contents: {0}'.format(e))
        # Not returning, since we need to close the changeset

    request = MethodRequest('{0}/api/0.6/changeset/{1}/close'.format(API_ENDPOINT, changeset_id), method=MethodRequest.PUT)
    try:
        opener.open(request)
    except Exception as e:
        print('Failed to close changeset (it will close automatically in an hour): {0}'.format(e))
    return ok
