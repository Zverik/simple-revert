#!/usr/bin/env python
import sys, urllib2, re
from common import obj_to_dict, dict_to_obj, upload_changes, API_ENDPOINT
from collections import deque

try:
  from lxml import etree
except ImportError:
  try:
    import xml.etree.cElementTree as etree
  except ImportError:
    import xml.etree.ElementTree as etree

MAX_DEPTH = 10

def parse_url(s):
  """Parses typeNNN or URL, returns a tuple of (type, id, version)."""
  s = s.strip().lower()
  t = i = v = None
  m = re.match(r'([nwr])[a-z]*[ /.-]*([0-9]+)', s)
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

def find_new_refs(old, last=None):
  """Finds references in old, which are not in last."""
  result = []
  if old['type'] == 'way' and 'refs' in old:
    nhash = {}
    if last is not None and 'refs' in last:
      for nd in last['refs']:
        nhash[nd] = True
    for nd in old['refs']:
      if nd not in nhash:
        result.append(('node', nd))
  elif old['type'] == 'relation' and 'refs' in old:
    mhash = {}
    if last is not None and 'refs' in last:
      for member in last['refs']:
        mhash[(member[0], member[1])] = True
    for member in old['refs']:
      if (member[0], member[1]) not in mhash:
        result.append((member[0], member[1]))
  return result

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
    for h in history[-MAX_DEPTH-1:]:
      print 'Version {0}: {1}changeset {2} on {3} by {4}'.format(h.get('version'), 'deleted in ' if h.get('visible') == 'false' else '', h.get('changeset'), h.get('timestamp'), h.get('user'))
    sys.exit(0)

  last_version = int(history[-1].get('version'))
  if obj_version < 0:
    obj_version = last_version + obj_version

  if obj_version <= 0 or obj_version >= last_version:
    if last_version == 1:
      print 'The object has only one version, nothing to restore.'
    else:
      print 'Incorrect version {0}, should be between 1 and {1}.'.format(obj_version, last_version - 1)
    sys.exit(1)

  if obj_version < last_version - MAX_DEPTH:
    print 'Restoring objects more than {0} versions back is blocked.'.format(MAX_DEPTH)
    sys.exit(1)

  # If we downloaded an incomplete history, add that version
  vref = None
  for h in history.iterchildren():
    if int(h.get('version')) == obj_version:
      vref = h
  if vref is None:
    response = opener.open('{0}/{1}/{2}/{3}'.format(API_ENDPOINT, obj_type, obj_id, obj_version))
    vref = etree.parse(response).getroot()[0]
    history.insert(0, vref)

  if vref.get('visible') == 'false':
    print 'Will not delete the object, use other means.'
    sys.exit(1)

  # Now building a list of changes, traversing all references, finding object to undelete
  obj = obj_to_dict(vref)
  obj['version'] = last_version
  changes = [obj]
  queue = deque()
  processed = {}
  queue.extend(find_new_refs(obj, obj_to_dict(history[-1])))
  while len(queue):
    qobj = queue.popleft()
    if qobj in processed:
      continue
    # Download last version and grab references from it
    try:
      response = opener.open('{0}/{1}/{2}'.format(API_ENDPOINT, qobj[0], qobj[1]))
      obj = obj_to_dict(etree.parse(response).getroot()[0])
    except urllib2.HTTPError as e:
      if e.code == 410:
        # Found a deleted object, download history and restore
        response = opener.open('{0}/{1}/{2}/history'.format(API_ENDPOINT, qobj[0], qobj[1]))
        ohist = etree.parse(response).getroot()
        i = len(ohist) - 1
        while i > 0 and ohist[i].get('visible') == 'false':
          i -= 1
        if ohist[i].get('visible') != 'true':
          print 'Could not find a non-deleted version of {0} {1}, referenced by the object. Sorry.'
          sys.exit(3)
        obj = obj_to_dict(ohist[i])
        obj['version'] = int(ohist[-1].get('version'))
        changes.append(obj)
      else:
        raise e
    queue.extend(find_new_refs(obj))
    processed[(obj['type'], obj['id'])] = True

  print changes
  tags = {
    'created_by': 'restore-version.py',
    'comment': 'Restoring version {0} of {1} {2}'.format(obj_version, obj_type, obj_id)
  }
  upload_changes(changes, tags)
