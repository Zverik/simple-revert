#!/usr/bin/env python
import sys, urllib2, getpass, base64
from collections import defaultdict
from lxml import etree

#API_ENDPOINT = 'https://api.openstreetmap.org/api/0.6'
API_ENDPOINT = 'http://master.apis.dev.openstreetmap.org/api/0.6'

# Copied from http://stackoverflow.com/a/3884771/1297601
class MethodRequest(urllib2.Request):
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
  """Converts XML object to a easy to use dict."""
  if obj is None:
    return None
  res = {}
  res['type'] = obj.tag
  res['id'] = int(obj.get('id'))
  res['version'] = int(obj.get('version'))
  if obj.tag == 'node' and 'lon' in obj.keys() and 'lat' in obj.keys():
    res['coord'] = (obj.get('lon'), obj.get('lat'))
  res['tags'] = { tag.get('k') : tag.get('v') for tag in obj.iterchildren('tag')}
  if obj.tag == 'way':
    res['refs'] = [x.get('ref') for x in obj.iterchildren('nd')]
  elif obj.tag == 'relation':
    res['refs'] = [(x.get('type'), x.get('ref'), x.get('role')) for x in obj.iterchildren('member')]
  return res

def xml_to_diff(obj, obj_prev):
  """Takes two XML trees and produces a diff."""
  diff = {}
  if obj_prev is None:
    if obj is None:
      return diff
    else:
      diff['action'] = 'delete'
  else:
    # Moving nodes back
    if 'coords' in obj_prev:
      if obj is None or obj['coords'] != obj_prev['coords']:
        diff['move'] = { 'from': obj['coords'], 'to': obj_prev['coords'] }

    # Restoring old tags
    if obj is None:
      for k in obj_prev['tags']:
        tdiff.append((k, obj_prev['tags'][k]))
    else:
      tdiff = []
      for k in obj['tags']:
        if k in obj_prev['tags'] and obj_prev['tags'][k] != obj['tags'][k]:
          tdiff.append((k, obj_prev['tags'][k]))
        elif k not in obj_prev['tags']:
          tdiff.append((k, None))
      for k in obj_prev['tags']:
        if k not in obj['tags']:
          tdiff.append((k, obj_prev['tags'][k]))
    if tdiff:
      diff['tags'] = tdiff

    # Keeping references for ways and relations
    refs = None
    if obj_prev['type'] in ('way', 'relation'):
      if obj is None:
        ref = obj_prev['refs']
      else:
        if obj_prev['refs'] != obj['refs']:
          raise Exception('Members for {0} {1} were changed, cannot roll that back'.format(obj['type'], obj['id']))
    if refs:
      diff['refs'] = refs

  return diff

def diff_to_xml(diff, obj_last):
  """Takes a diff and the last version of the object, and produces an initial object xml from it.
  Changeset version is coded as $CHANGESET$."""
  # TODO
  return ''

if __name__ == '__main__':
  if len(sys.argv) < 2:
    print 'This script reverts simple OSM changesets. It will tell you if it fails.'
    print 'Usage: {0} <changeset_id> [<changeset_id> ...]'.format(sys.argv[0])
    sys.exit(1)

  opener = urllib2.build_opener(urllib2.HTTPSHandler(debuglevel=1))
  changesets = [int(x) for x in sys.argv[1:]]
  diffs = defaultdict(dict)

  for changeset_id in changesets:
    try:
      # Download a changeset
      response = opener.open('{0}/changeset/{1}/download'.format(API_ENDPOINT, changeset_id))
      root = etree.parse(response).getroot()
      # Iterate over each object, download previous version (unless it's creation) and make a diff 
      for action in root:
        for obj_xml in action:
          obj = obj_to_dict(obj_xml)
          if obj['version'] > 1:
            # Download the previous version
            try:
              response = opener.open('{0}/{1}/{2}/{3}'.format(API_ENDPOINT, obj['type'], obj['id'], obj['version'] - 1))
              obj_prev = obj_to_dict(etree.parse(response).getroot()[0])
            except urllib2.HTTPError as e:
              if e.code == 410 or e.code == 404:
                obj_prev = None
              else:
                raise e
          else:
            obj_prev = None
          diffs[(obj['type'], obj['id'])][obj['version']] = xml_to_diff(None if action.tag == 'delete' else obj, obj_prev)
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

  # TODO: Sort diff, so deleted nodes are first
  print diffs

  xml = []
  for obj, change in diffs.iteritems():
    try:
      # Download the latest version of an object
      try:
        response = opener.open('{0}/{1}/{2}'.format(API_ENDPOINT, obj[0], obj[1]))
        obj = obj_to_dict(etree.parse(response).getroot()[0])
      except urllib2.HTTPError as e:
        if e.code == 410 or e.code == 404:
          obj = None
        else:
          raise e
      xml.append(diff_to_xml(change, obj))
    except Exception as e:
      print 'Failed to download the latest version of {0} {1}: {2}'.format(obj[0], obj[1], e)
      sys.exit(2)

  auth_header = read_auth()
  opener.addheaders = [('Authorization', auth_header)]

  # Create changeset
  create_xml = """<osm><changeset>
  <tag k="created_by" v="simple-revert.py" />
  <tag k="comment" v="Reverting {0}" />
  </changeset></osm>""".format(', '.join([str(x) for x in changesets]))
  request = MethodRequest(API_ENDPOINT + '/changeset/create', create_xml, method=MethodRequest.PUT)
  try:
    changeset_id = int(opener.open(request).read())
    print 'Writing to changeset {0}'.format(changeset_id)
  except Exception as e:
    print 'Failed to create changeset', e
    sys.exit(0)

  diff_xml = ''.join(['<osmChange version="0.6">'] + [x.replace('$CHANGESET$', str(changeset_id)) for x in xml] + ['</osmChange>'])
  request = MethodRequest('{0}/changeset/{1}/upload'.format(API_ENDPOINT, changeset_id), diff_xml, method=MethodRequest.POST)
  try:
    response = opener.open(request)
  except Exception as e:
    print 'Failed to upload changetset contents', e
    # Not returning, since we need to close the changeset

  request = MethodRequest('{0}/changeset/{1}/close'.format(API_ENDPOINT, changeset_id), method=MethodRequest.PUT)
  try:
    response = opener.open(request)
  except Exception as e:
    print 'Failed to close changeset (it will close automatically in an hour)', e

  print 'Done.'
