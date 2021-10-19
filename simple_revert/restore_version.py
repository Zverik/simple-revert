import logging
import sys
import re
from collections import deque
from .common import (
    obj_to_dict,
    upload_changes,
    api_request,
    changes_to_osc,
    HTTPError,
    etree
)

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


def safe_print(s):
    sys.stderr.write(s + '\n')


def main():
    if len(sys.argv) < 2:
        print('Restores a specific version of a given object, undeleting all missing references')
        print()
        print('Usage: {0} {{<typeNNN>|<url>}} [{{<version>|-N}}]'.format(sys.argv[0]))
        print()
        print('URLs both from osm.org and api.osm.org (even with version) are accepted.')
        print('Use -1 to revert last version (e.g. undelete an object).')
        print('Omit version number to see an object history.')
        sys.exit(1)

    logging.basicConfig(level=logging.INFO, format='%(message)s')
    obj_type, obj_id, obj_version = parse_url(sys.argv[1])
    if obj_type is None or obj_id is None:
        safe_print('Please specify correct object type and id.')
        sys.exit(1)
    if len(sys.argv) > 2:
        obj_version = int(sys.argv[2])

    # Download full object history
    # If we fail, revert to a given version blindly
    history = None
    safe_print('Downloading history of {0} {1}'.format(obj_type, obj_id))
    try:
        history = api_request('{0}/{1}/history'.format(obj_type, obj_id))
    except HTTPError as e:
        if e.code not in [408, 500, 503, 504]:
            raise IOError('Unexpected error: {}'.format(e))
        # Failed to read the complete history due to a timeout, read only two versions
        safe_print('History is too large to download. Querying the last version only.')
        history = etree.Element('osm')
        try:
            obj = api_request('{0}/{1}'.format(obj_type, obj_id))[0]
            history.append(obj)
        except HTTPError:
            if e.code != 410:
                raise IOError('Unexpected error: {}'.format(e))
            safe_print('To restore a deleted version, we need to know the last ' +
                       'version number, and we failed.')
            sys.exit(2)

    if obj_version is None:
        # Print history and exit
        for h in history[-MAX_DEPTH - 1:]:
            print('Version {0}: {1}changeset {2} on {3} by {4}'.format(
                h.get('version'), 'deleted in ' if h.get('visible') == 'false' else '',
                h.get('changeset'), h.get('timestamp'), h.get('user')))
        sys.exit(0)

    last_version = int(history[-1].get('version'))
    if obj_version < 0:
        obj_version = last_version + obj_version

    if obj_version <= 0 or obj_version >= last_version:
        if last_version == 1:
            safe_print('The object has only one version, nothing to restore.')
        else:
            safe_print('Incorrect version {0}, should be between 1 and {1}.'.format(
                obj_version, last_version - 1))
        sys.exit(1)

    if obj_version < last_version - MAX_DEPTH:
        safe_print('Restoring objects more than {0} versions back is blocked.'.format(MAX_DEPTH))
        sys.exit(1)

    # If we downloaded an incomplete history, add that version
    vref = None
    for h in history:
        if int(h.get('version')) == obj_version:
            vref = h
    if vref is None:
        vref = api_request('{0}/{1}/{2}'.format(obj_type, obj_id, obj_version))[0]
        history.insert(0, vref)

    if vref.get('visible') == 'false':
        safe_print('Will not delete the object, use other means.')
        sys.exit(1)

    # Now building a list of changes, traversing all references, finding objects to undelete
    obj = obj_to_dict(vref)
    obj['version'] = last_version
    changes = [obj]
    queue = deque()
    processed = {}
    queue.extend(find_new_refs(obj, obj_to_dict(history[-1])))
    singleref = False
    while len(queue):
        qobj = queue.popleft()
        if qobj in processed:
            continue
        singleref = True
        sys.stderr.write('\rDownloading referenced {0}, {1} left, {2} to undelete{3}'.format(
            qobj[0], len(queue), len(changes) - 1, ' ' * 10))
        sys.stderr.flush()
        # Download last version and grab references from it
        try:
            obj = obj_to_dict(api_request('{0}/{1}'.format(qobj[0], qobj[1]))[0])
        except HTTPError as e:
            if e.code != 410:
                raise IOError('Unexpected error: {}'.format(e))
            # Found a deleted object, download history and restore
            ohist = api_request('{0}/{1}/history'.format(qobj[0], qobj[1]))
            i = len(ohist) - 1
            while i > 0 and ohist[i].get('visible') == 'false':
                i -= 1
            if ohist[i].get('visible') != 'true':
                safe_print('Could not find a non-deleted version of {0} {1}, ' +
                           'referenced by the object. Sorry.')
                sys.exit(3)
            obj = obj_to_dict(ohist[i])
            obj['version'] = int(ohist[-1].get('version'))
            changes.append(obj)
            queue.extend(find_new_refs(obj))
        processed[(obj['type'], obj['id'])] = True
    if singleref:
        sys.stderr.write('\n')

    if not changes:
        sys.stderr.write('No changes to upload.\n')
    elif sys.stdout.isatty():
        tags = {
            'created_by': 'restore-version.py',
            'comment': 'Restoring version {0} of {1} {2}'.format(obj_version, obj_type, obj_id)
        }
        upload_changes(changes, tags)
    else:
        print(changes_to_osc(changes))
