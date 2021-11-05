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
MAX_OBJECTS = 20  # might reach a max comment length first


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


def safe_print(s=''):
    sys.stderr.write(s + '\n')


def get_obj_history(obj_type, obj_id, obj_version):
    """ Download object history; params are from tuple returned by parse_url.
    If obj_version None, prints history and exits.
    Returns object's history list.
    """
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

    return history


def get_obj_version(obj_type, obj_id, obj_version, obj_history):
    """ Get requested object version, or exit(1). Updates obj_version if negative.
    Returns tuple (obj_version, last_version, vref).
    """
    last_version = int(obj_history[-1].get('version'))
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
    for h in obj_history:
        if int(h.get('version')) == obj_version:
            vref = h
    if vref is None:
        vref = api_request('{0}/{1}/{2}'.format(obj_type, obj_id, obj_version))[0]
        obj_history.insert(0, vref)

    if vref.get('visible') == 'false':
        safe_print('Will not delete the object, use other means.')
        sys.exit(1)

    return(obj_version, last_version, vref)


def build_undelete_changes(restore_objs):
    """ For each (obj_type, obj_id, obj_version, obj_history) item in restore_objs,
    traverse its obj_history to build changeset to undelete it.
    Returns tuple (changes or [], comment).
    """
    comment = ""
    changes = []
    queue = deque()

    for obj_item in restore_objs:
        obj_type, obj_id, obj_version, obj_history = obj_item[0:4]

        obj_version, last_version, vref = get_obj_version(
            obj_type, obj_id, obj_version, obj_history)

        # Now building a list of changes, traversing all references, finding objects to undelete
        obj = obj_to_dict(vref)
        obj['version'] = last_version
        changes.append(obj)
        queue.extend(find_new_refs(obj, obj_to_dict(obj_history[-1])))
        comment_part = 'version {0} of {1} {2}'.format(obj_version, obj_type, obj_id)
        if len(comment):
            comment += ", " + comment_part
        else:
            comment = "Restoring " + comment_part

    processed = {}
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
        # update download counts, in case last item was already processed
        sys.stderr.write('\rDownloading referenced {0}, {1} left, {2} to undelete{3}'.format(
            qobj[0], len(queue), len(changes) - 1, ' ' * 10))
        sys.stderr.flush()
        sys.stderr.write('\n')

    return (changes, comment)


def print_usage_and_exit():
    print('Restores a specific version of given objects, undeleting all missing references')
    print()
    print('Usage:')
    print('  {0} {{<typeNNN|<url>}}  to see the object\'s history.'.format(sys.argv[0]))
    print('  {0} {{<typeNNN|<url>}} {{<version>|-N}}'.format(sys.argv[0]) +
          '  to revert an object to an earlier version.')
    print('  {0} {{<obj>}} {{<ver>}} {{<obj>}} {{<ver>}} ...'.format(sys.argv[0]) +
          '  to restore multiple objects.')
    print()
    print('URLs both from osm.org and api.osm.org (even with version) are accepted.')
    print('Use -1 to revert last version (e.g. undelete an object).')
    sys.exit(1)


def main():
    if len(sys.argv) < 2:
        print_usage_and_exit()

    logging.basicConfig(level=logging.INFO, format='%(message)s')

    restore_objs = []
    i = 1
    while (i < len(sys.argv)):
        obj_type, obj_id, obj_version = parse_url(sys.argv[i])
        i += 1
        if obj_type is None or obj_id is None:
            safe_print('Please specify correct object type and id.')
            sys.exit(1)
        if obj_version is None:
            if len(sys.argv) == 2:
                # print single history, exit(0)
                get_obj_history(obj_type, obj_id, None)
            elif i < len(sys.argv):
                try:
                    obj_version = int(sys.argv[i])
                    i += 1
                except ValueError:
                    pass
            if obj_version is None:
                safe_print('Expected version number after {0}.'.format(
                    sys.argv[i - 1]))
                safe_print()
                print_usage_and_exit()
        restore_objs.append([obj_type, obj_id, obj_version])

    if len(restore_objs) > MAX_OBJECTS:
        safe_print('Restoring more than {0} objects is blocked.'.format(MAX_OBJECTS))
        sys.exit(1)

    for olist in restore_objs:
        history = get_obj_history(olist[0], olist[1], olist[2])
        olist.append(history)

    changes, comment = build_undelete_changes(restore_objs)

    if not changes:
        sys.stderr.write('No changes to upload.\n')
    elif sys.stdout.isatty():
        tags = {
            'created_by': 'restore-version.py',
            'comment': comment
        }
        upload_changes(changes, tags)
    else:
        print(changes_to_osc(changes))
