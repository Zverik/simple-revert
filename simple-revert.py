#!/usr/bin/env python
import sys, urllib2
from collections import defaultdict
from copy import deepcopy
from common import obj_to_dict, dict_to_obj, upload_changes, API_ENDPOINT

try:
  from lxml import etree
except ImportError:
  try:
    import xml.etree.cElementTree as etree
  except ImportError:
    import xml.etree.ElementTree as etree

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

  opener = urllib2.build_opener()
  changesets = [int(x) for x in sys.argv[1:]]
  ch_users = {}
  diffs = defaultdict(dict)

  print 'Downloading changesets'
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

  print 'Reverting changes'
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
        obj_new = { 'type': obj['type'], 'id': obj['id'], 'version': obj['version'], 'deleted': True }
        changes.append(obj_new)
      elif len(change) == 2 and change[1] == 'delete':
        # Restore only if the object is still absent
        if obj['deleted']:
          obj_new = change[1][1]
          obj_new['version'] = obj['version']
          changes.append(obj_new)
      else:
        obj_new = apply_diff(change, deepcopy(obj))
        if obj_new != obj:
          changes.append(obj_new)
    except Exception as e:
      print 'Failed to download the latest version of {0} {1}: {2}'.format(obj[0], obj[1], e)
      sys.exit(2)

  tags = {
    'created_by': 'simple_revert.py',
    'comment': 'Reverting {0}'.format(', '.join(['{0} by {1}'.format(str(x), ch_users[x]) for x in changesets]))
  }
  upload_changes(changes, tags)
