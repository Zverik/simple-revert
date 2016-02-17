#!/usr/bin/env python
import sys, urllib2, re
from lxml import etree
from common import obj_to_dict, dict_to_obj, upload_changes, API_ENDPOINT

def parse_url(s):
  """Parses typeNNN or URL, returns a tuple of (type, id, version)."""
  s = s.strip().lower()
  t = i = v = None
  m = re.match(r'([nwr])[a-z]+[ /.-]+([0-9]+)', s)
  if m:
    t = m.group(1)
    i = int(m.group(2))
    if t == 'n':
      t = 'node'
    elif t == 'w':
      t = 'way'
    else:
      t = 'relation'
  else:
    m = re.search(r'/(node|way|relation)/([0-9]+)(?:/([0-9+]))?', s)
    if m:
      t = m.group(1)
      i = int(m.group(2))
      if m.lastindex > 2:
        v = int(m.group(3))
  return (t, i, v)

if __name__ == '__main__':
  if len(sys.argv) < 2:
    print 'Restores a specific version of a given object, undeleting all missing references'
    print
    print 'Usage: {0} {{<typeNNN>|<url>}} [{{<version>|-N}}]'.format(sys.argv[0])
    print
    print 'URLs both from osm.org and api.osm.org (even with version) are accepted.'
    print 'Use -1 to revert last version (e.g. undelete an object).'
    print 'Omit version number to see an object history.'
    sys.exit(1)

  obj_type, obj_id, obj_version = parse_url(sys.argv[1])
  if obj_type is None or obj_id is None:
    print 'Please specify correct object type and id.'
    sys.exit(1)
  if len(sys.argv) > 2:
    obj_version = int(sys.argv[2])

  # Download full object history
  # If we fail, revert to a given version blindly
  opener = urllib2.build_opener()
  history = None
  try:
    response = opener.open('{0}/{1}/{2}/history'.format(API_ENDPOINT, obj_type, obj_id))
    history = etree.parse(response).getroot()
  except urllib2.HTTPError as e:
    if e.code in (408, 500, 503, 504):
      # Failed to read the complete history due to a timeout, read only two versions
      print 'History is too large to download. Querying the last version only.'
      history = etree.Element('osm')
      try :
        response = opener.open('{0}/{1}/{2}'.format(API_ENDPOINT, obj_type, obj_id))
        obj = etree.parse(response).getroot()[0]
        history.append(obj)
      except urllib2.HTTPError as e:
        if e.code == 410:
          print 'To restore a deleted version, we need to know the last version number, and we failed.'
          sys.exit(2)
        else:
          raise e
    else:
      raise e

  if obj_version is None:
    # Print history and exit
    for h in history.iterchildren():
      print 'Version {0}: {1}changeset {2} on {3} by {4}'.format(h.get('version'), 'deleted in ' if h.get('visible') == 'false' else '', h.get('changeset'), h.get('timestamp'), h.get('user'))
    sys.exit(0)

  last_version = history[-1].get('version')
  if obj_version < 0:
    obj_version = last_version + obj_version

  if obj_version < 0 or obj_version >= last_version:
    print 'Incorrect version {0}, should be between 1 and {1}.'.format(obj_version, last_version - 1)
    sys.exit(1)

  # If we downloaded an incomplete history, add that version
  found_version = False
  for h in history.iterchildren():
    if int(h.get('version')) == obj_version:
      found_version = True
  if not found_version:
    response = opener.open('{0}/{1}/{2}/{3}'.format(API_ENDPOINT, obj_type, obj_id, obj_version))
    history.insert(0, etree.parse(response).getroot()[0])

  # TODO
  raise Exception('Not implemented')
