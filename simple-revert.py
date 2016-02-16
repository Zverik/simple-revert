#!/usr/bin/env python
import sys, urllib2, getpass, base64
from collections import defaultdict
from copy import deepcopy
from lxml import etree

API_ENDPOINT = 'https://api.openstreetmap.org/api/0.6'
#API_ENDPOINT = 'http://master.apis.dev.openstreetmap.org/api/0.6'

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
    login = raw_input('OSM Login: ')
    auth_header = 'Basic {0}'.format(base64.b64encode('{0}:{1}'.format(login, getpass.getpass('OSM Password: '))))
    try:
      request = urllib2.Request(API_ENDPOINT + '/user/details')
      request.add_header('Authorization', auth_header)
      result = urllib2.urlopen(request)
      ok = 'account_created' in result.read()
    except Exception as e:
      print e
    if not ok:
      print 'You must have mistyped. Please try again.'
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
    res['coord'] = (obj.get('lon'), obj.get('lat'))
  res['tags'] = { tag.get('k') : tag.get('v') for tag in obj.iterchildren('tag')}
  if obj.tag == 'way':
    res['refs'] = [x.get('ref') for x in obj.iterchildren('nd')]
  elif obj.tag == 'relation':
    res['refs'] = [(x.get('type'), x.get('ref'), x.get('role')) for x in obj.iterchildren('member')]
  return res

def dict_to_obj(obj):
  """Converts object dict back to an XML element."""
  if obj is None:
    return None
  res = etree.Element(obj['type'], {'id': str(obj['id']), 'version': str(obj['version'])})
  res.set('visible', 'false' if obj['deleted'] else 'true')
  if 'coord' in obj:
    res.set('lon', obj['coord'][0])
    res.set('lat', obj['coord'][1])
  for k, v in obj['tags'].iteritems():
    res.append(etree.Element('tag', {'k': k, 'v': v}))
  if obj['type'] == 'way':
    for nd in obj['refs']:
      res.append(etree.Element('nd', {'ref': nd}))
  elif obj['type'] == 'relation':
    for member in obj['refs']:
      res.append(etree.Element('member', {'type': member[0], 'ref': member[1], 'role': member[2]}))
  return res

def make_diff(obj, obj_prev):
  """Takes two object dicts and produces a diff."""
  diff = [('version', obj['version'])]
  if obj_prev is None or obj_prev['deleted']:
    if obj['deleted']:
      return diff
    else:
      diff.append(('create', obj))
  elif obj['deleted']:
    diff.append(('delete', obj_prev))
  else:
    # Both objects are present, compare them
    # Moving nodes back
    if 'coords' in obj_prev:
      if obj['coords'] != obj_prev['coords']:
        diff.append(('move', obj_prev['coords'], obj['coords']))

    # Restoring old tags
    for k in obj['tags']:
      if k in obj_prev['tags'] and obj_prev['tags'][k] != obj['tags'][k]:
        diff.append(('tag', k, obj_prev['tags'][k], obj['tags'][k]))
      elif k not in obj_prev['tags']:
        diff.append(('tag', k, None, obj['tags'][k]))
    for k in obj_prev['tags']:
      if k not in obj['tags']:
        diff.append(('tag', k, obj_prev['tags'][k], None))

    # Keeping references for ways and relations
    if 'refs' in obj and obj_prev['refs'] != obj['refs']:
      raise Exception('Members for {0} {1} were changed, cannot roll that back'.format(obj['type'], obj['id']))

  return diff

def merge_diffs(diff, diff_newer):
  """Merge two sequential diffs."""
  raise Exception('Merging diffs is not supported yet')

def apply_diff(diff, obj):
  """Takes a diff and the last version of the object, and produces an initial object from it."""
  for change in diff:
    if change[0] == 'version':
      dver = change[1]
    elif change[0] == 'move':
      if 'coords' not in obj:
        raise Exception('Move action found for {0} {1}'.format(obj['type'], obj['id']))
      # If an object was moved after the last change, keep the coordinates
      if dver == obj['version'] or change[2] == obj['coords']:
        obj['coords'] = change[1]
    elif change[0] == 'tag':
      if change[1] in obj['tags']:
        if change[3] is None:
          pass # Somebody has already restored the tag
        elif obj['tags'][change[1]] == change[3]:
          if change[2] is None:
            del obj['tags'][change[1]]
          else:
            obj['tags'][change[1]] = change[2]
      else:
        # If a modified tag was deleted after, do not restore it
        if change[3] is None:
          obj['tags'][change[1]] = change[2]
    else:
      raise Exception('Unknown or unprocessed by apply_diff change type: {0}'.format(change[0]))
  return obj

if __name__ == '__main__':
  if len(sys.argv) < 2:
    print 'This script reverts simple OSM changesets. It will tell you if it fails.'
    print 'Usage: {0} <changeset_id> [<changeset_id> ...]'.format(sys.argv[0])
    sys.exit(1)

  debuglevel = 0
  opener = urllib2.build_opener(urllib2.HTTPSHandler(debuglevel=debuglevel), urllib2.HTTPHandler(debuglevel=debuglevel))
  changesets = [int(x) for x in sys.argv[1:]]
  ch_users = {}
  diffs = defaultdict(dict)

  for changeset_id in changesets:
    try:
      # Download a changeset
      response = opener.open('{0}/changeset/{1}/download'.format(API_ENDPOINT, changeset_id))
      root = etree.parse(response).getroot()
      # Iterate over each object, download previous version (unless it's creation) and make a diff 
      for action in root:
        for obj_xml in action:
          if changeset_id not in ch_users:
            ch_users[changeset_id] = obj_xml.get('user')
          obj = obj_to_dict(obj_xml)
          if obj['version'] > 1:
            # Download the previous version
            response = opener.open('{0}/{1}/{2}/{3}'.format(API_ENDPOINT, obj['type'], obj['id'], obj['version'] - 1))
            obj_prev = obj_to_dict(etree.parse(response).getroot()[0])
          else:
            obj_prev = None
          diffs[(obj['type'], obj['id'])][obj['version']] = make_diff(obj, obj_prev)
    except Exception as e:
      print 'Failed to download changeset {0}: {1}'.format(changeset_id, e)
      import traceback
      traceback.print_exc()
      sys.exit(2)

  if not diffs:
    print 'No changes to revert.'
    sys.exit(0)

  # merge versions of same objects in diffs
  for k, versions in diffs.iteritems():
    if len(versions) > 1:
      raise Exception('Multiple versions of a same object ({0} {1}) are not supported yet.'.format(k[0], k[1]))
    diffs[k] = versions.values()[0]

  changes = []
  for obj, change in diffs.iteritems():
    try:
      # Download the latest version of an object
      try:
        response = opener.open('{0}/{1}/{2}'.format(API_ENDPOINT, obj[0], obj[1]))
        obj = obj_to_dict(etree.parse(response).getroot()[0])
      except urllib2.HTTPError as e:
        if e.code == 410:
          # Read the full history to get the latest version
          response = opener.open('{0}/{1}/{2}/history'.format(API_ENDPOINT, obj[0], obj[1]))
          obj = obj_to_dict(etree.parse(response).getroot()[-1])
        else:
          raise e

      # Apply the change
      if len(change) == 2 and change[1] == 'create':
        obj_new = { 'type': obj['type'], 'id': obj['id'], 'version': obj['version'] }
        changes.append(('delete', obj_new))
      elif len(change) == 2 and change[1] == 'delete':
        # Restore only if the object is still absent
        if obj['deleted']:
          obj_new = change[1][1]
          obj_new['version'] = obj['version']
          changes.append(('modify', obj_new))
      else:
        obj_new = apply_diff(change, deepcopy(obj))
        if obj_new != obj:
          changes.append(('modify', obj_new))
    except Exception as e:
      print 'Failed to download the latest version of {0} {1}: {2}'.format(obj[0], obj[1], e)
      sys.exit(2)

  if not changes:
    print 'No changes to upload.'
    sys.exit(0)

  # Sort changes, so created nodes are first, and deleted are last
  def change_as_key(ch):
    act = ['create', 'modify', 'delete'].index(ch[0])
    typ = ['node', 'way', 'relation'].index(ch[1]['type'])
    if act == 2:
      typ = 2 - typ
    return '{0}{1}{2}'.format(act, typ, ch[1]['id'])

  changes.sort(key=change_as_key)

  # Now we need the OSM credentials
  auth_header = read_auth()
  opener.addheaders = [('Authorization', auth_header)]

  # Create changeset
  create_xml = etree.Element('osm')
  ch = etree.SubElement(create_xml, 'changeset')
  ch.append(etree.Element('tag', {'k': 'created_by', 'v': 'simple-revert.py'})
  ch.append(etree.Element('tag', {'k': 'comment', 'v': 'Reverting {0}'.format(', '.join(['{0} by {1}'.format(str(x), ch_users[x]) for x in changesets]))}))
  request = MethodRequest(API_ENDPOINT + '/changeset/create', etree.tostring(create_xml), method=MethodRequest.PUT)
  try:
    changeset_id = int(opener.open(request).read())
    print 'Writing to changeset {0}'.format(changeset_id)
  except Exception as e:
    print 'Failed to create changeset', e
    sys.exit(0)

  # Produce osmChange XML and upload it
  osc = etree.Element('osmChange', {'version': '0.6'})
  for c in changes:
    act = etree.SubElement(osc, c[0])
    el = dict_to_obj(c[1])
    el.set('changeset', str(changeset_id))
    act.append(el)

  request = MethodRequest('{0}/changeset/{1}/upload'.format(API_ENDPOINT, changeset_id), etree.tostring(osc), method=MethodRequest.POST)
  try:
    response = opener.open(request)
  except urllib2.HTTPError as e:
    print 'Server rejected the changeset:', e
  except Exception as e:
    print 'Failed to upload changetset contents:', e
    # Not returning, since we need to close the changeset

  request = MethodRequest('{0}/changeset/{1}/close'.format(API_ENDPOINT, changeset_id), method=MethodRequest.PUT)
  try:
    response = opener.open(request)
  except Exception as e:
    print 'Failed to close changeset (it will close automatically in an hour)', e

  print 'Done.'
