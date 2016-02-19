#!/usr/bin/env python
import sys
from collections import defaultdict
from copy import deepcopy
from urllib import quote
from common import safe_print, obj_to_dict, dict_to_obj, upload_changes, api_download, HTTPError

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
      diff.append(('refs', obj_prev['refs'], obj['refs']))

  return diff

def merge_diffs(diff, diff_newer):
  """Merge two sequential diffs."""
  if diff is None:
    return diff_newer
  result = [diff_newer[0]]
  # First, resolve creating and deleting
  if len(diff) == 2 and diff[1][0] == 'create':
    if len(diff_newer) == 2 and diff_newer[1][0] == 'create':
      pass
    elif len(diff_newer) == 2 and diff_newer[1][0] == 'delete':
      pass
    else:
      pass
  elif len(diff) == 2 and diff[1][0] == 'delete':
    if len(diff_newer) == 2 and diff_newer[1][0] == 'create':
      pass
    elif len(diff_newer) == 2 and diff_newer[1][0] == 'delete':
      pass
    else:
      pass
  else:
    if len(diff_newer) == 2 and diff_newer[1][0] == 'create':
      pass
    elif len(diff_newer) == 2 and diff_newer[1][0] == 'delete':
      pass
    else:
      # O(n^2) complexity, because diffs are usually small
      for change in diff:
        if change[0] == 'move' or change[0] == 'refs':
          op_newer = None
          for k in diff_newer:
            if k[0] == change[0]:
              op_newer = k
          if op_newer is None:
            result.append(change)
          elif change[2] == op_newer[1]:
            result.append((change[0], change[1], op_newer[2]))
          else:
            result.append(op_newer)
        elif change[0] == 'tag':
          op_newer = None
          for k in diff_newer:
            if k[0] == 'tag' and k[1] == change[1]:
              op_newer = k
          if op_newer is None:
            result.append(change)
          elif change[3] == op_newer[2]:
            result.append(('tag', change[1], change[2], op_newer[3]))
          else:
            result.append(op_newer)
        else:
          raise Exception('Missing processor for merging {0} operation'.format(change[0]))

  if len(result) > 1:
    return result
  # Creations and deletions will cause this exception for now
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
    elif change[0] == 'refs':
      if obj['refs'] != change[2]:
        raise Exception('Members for {0} {1} were changed, cannot roll that back'.format(obj['type'], obj['id']))
    else:
      raise Exception('Unknown or unprocessed by apply_diff change type: {0}'.format(change[0]))
  return obj

if __name__ == '__main__':
  if len(sys.argv) < 2:
    print 'This script reverts simple OSM changesets. It will tell you if it fails.'
    print 'Usage: {0} <changeset_id> [<changeset_id> ...]'.format(sys.argv[0])
    print 'To list recent changesets by a user: {0} <user_name>'.format(sys.argv[0])
    sys.exit(1)

  if len(sys.argv) == 2 and not sys.argv[1].isdigit():
    # We have a display name, show their changesets
    try:
      root = api_download('changesets?closed=true&display_name={0}'.format(quote(sys.argv[1])), throw=[404])
      for changeset in root[:15]:
        created_by = '???'
        comment = '<no comment>'
        for tag in changeset.iterchildren('tag'):
          if tag.get('k') == 'created_by':
            created_by = tag.get('v').encode('utf-8')
          elif tag.get('k') == 'comment':
            comment = tag.get('v').encode('utf-8')
        print 'Changeset {0} created on {1} with {2}:\t{3}'.format(changeset.get('id'), changeset.get('created_at'), created_by, comment)
    except HTTPError as e:
      print 'No such user found.'
    sys.exit(0)

  changesets = [int(x) for x in sys.argv[1:]]
  ch_users = {}
  diffs = defaultdict(dict)

  for changeset_id in changesets:
    info_str = '\rDownloading changeset {0}'.format(changeset_id)
    sys.stderr.write(info_str)
    sys.stderr.flush()
    root = api_download('changeset/{0}/download'.format(changeset_id),
        sysexit_message='Failed to download changeset {0}'.format(changeset_id))
    # Iterate over each object, download previous version (unless it's creation) and make a diff 
    count = total = 0
    for action in root:
      if action.tag != 'create':
        total += len(action)
    for action in root:
      for obj_xml in action:
        if action.tag != 'create':
          count += 1
        if changeset_id not in ch_users:
          ch_users[changeset_id] = obj_xml.get('user')
        obj = obj_to_dict(obj_xml)
        if obj['version'] > 1:
          sys.stderr.write('{0}, historic version of {1} {2} [{3}/{4}]{5}'.format(info_str, obj['type'], obj['id'], count, total, ' ' * 15))
          sys.stderr.flush()
          try:
            obj_prev = obj_to_dict(api_download('{0}/{1}/{2}'.format(obj['type'], obj['id'], obj['version'] - 1), throw=[403])[0])
          except HTTPError:
            safe_print('\nCannot revert redactions, see version {0} at https://openstreetmap.org/{1}/{2}/history'.format(obj['version'] - 1, obj['type'], obj['id']))
            sys.exit(3)
        else:
          obj_prev = None
        diffs[(obj['type'], obj['id'])][obj['version']] = make_diff(obj, obj_prev)
    sys.stderr.write('\n')

  if not diffs:
    safe_print('No changes to revert.')
    sys.exit(0)

  # merge versions of same objects in diffs
  for k in diffs:
    diff = None
    for v in sorted(diffs[k].keys()):
      diff = merge_diffs(diff, diffs[k][v])
    diffs[k] = diff

  info_str = '\rReverting changes'
  sys.stderr.write(info_str)
  sys.stderr.flush()
  changes = []
  count = 0
  for kobj, change in diffs.iteritems():
    count += 1
    if change is None:
      continue
    try:
      # Download the latest version of an object
      sys.stderr.write('{0}, downloading {1} {2} [{3}/{4}]{5}'.format(info_str, kobj[0], kobj[1], count, len(diffs), ' ' * 15))
      sys.stderr.flush()
      try:
        obj = obj_to_dict(api_download('{0}/{1}'.format(kobj[0], kobj[1]), throw=[410])[0])
      except HTTPError as e:
        # Read the full history to get the latest version
        obj = obj_to_dict(api_download('{0}/{1}/history'.format(kobj[0], kobj[1]))[-1])

      # Apply the change
      obj_new = None
      if len(change) == 2 and change[1][0] == 'create':
        if not obj['deleted']:
          obj_new = { 'type': obj['type'], 'id': obj['id'], 'deleted': True }
      elif len(change) == 2 and change[1][0] == 'delete':
        # Restore only if the object is still absent
        if obj['deleted']:
          obj_new = change[1][1]
        else:
          # Controversial, but I've decided to replace the object with the old one in this case
          obj_new = change[1][1]
      else:
        obj_new = apply_diff(change, deepcopy(obj))

      if obj_new is not None:
        obj_new['version'] = obj['version']
        if obj_new != obj:
          changes.append(obj_new)
    except Exception as e:
      safe_print('\nFailed to download the latest version of {0} {1}: {2}'.format(kobj[0], kobj[1], e))
      sys.exit(2)
  sys.stderr.write('\n')

  tags = {
    'created_by': 'simple_revert.py',
    'comment': 'Reverting {0}'.format(', '.join(['{0} by {1}'.format(str(x), ch_users[x]) for x in changesets]))
  }
  upload_changes(changes, tags)
