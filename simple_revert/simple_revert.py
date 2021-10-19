import sys
import logging
from collections import defaultdict
from copy import deepcopy
from .common import (
    obj_to_dict,
    upload_changes,
    api_request,
    HTTPError,
    RevertError,
    changes_to_osc
)


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
        if (len(diff_newer) == 2 and diff_newer[0][1] == diff[0][1] + 1 and
                diff_newer[1][0] == 'delete'):
            # A special case: deletion negates creation
            return None
        # On creation, return the first diff: reverting it means deleting the object. No options
        return diff
    elif len(diff) == 2 and diff[1][0] == 'delete':
        if len(diff_newer) == 2 and diff_newer[1][0] == 'create':
            # Deletion and creation basically means changing some fields. Make a proper diff
            return make_diff(diff_newer[1][1], diff[1][1])
        elif len(diff_newer) == 2 and diff_newer[1][0] == 'delete':
            # Two deletions, return the earlier one
            return diff
        else:
            # Undoing deletion will clear any changes from the second diff
            return diff
    else:
        if len(diff_newer) == 2 and diff_newer[1][0] == 'create':
            # We assume the second change was a simple undeletion, so we ignore it.
            # Not going to delete
            return diff
        elif len(diff_newer) == 2 and diff_newer[1][0] == 'delete':
            # This is a tough one. We need to both restore the deleted object
            # and apply a diff on top
            result.append(('delete', apply_diff(diff, diff_newer[1][1])))
        else:
            # O(n^2) complexity, because diffs are usually small
            moved = False
            tags = set()
            for change in diff:
                if change[0] == 'version':
                    pass
                elif change[0] == 'move' or change[0] == 'refs':
                    moved = True
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
                    tags.add(change[1])
                    op_newer = None
                    for k in diff_newer:
                        if k[0] == 'tag' and k[1] == change[1]:
                            op_newer = k
                    if op_newer is None:
                        result.append(change)
                    elif change[2] == op_newer[3]:
                        pass  # Tag value was reverted
                    elif change[3] == op_newer[2]:
                        result.append(('tag', change[1], change[2], op_newer[3]))
                    else:
                        result.append(op_newer)
                else:
                    raise Exception('Missing processor for merging {0} operation'.format(change[0]))
            # Process changes from diff_newer
            for op_newer in diff_newer:
                if op_newer[0] == 'move' and not moved:
                    result.append(op_newer)
                elif op_newer[0] == 'tag' and op_newer[1] not in tags:
                    result.append(op_newer)

    if len(result) > 1:
        return result
    # We didn't come up with any changes, return empty value
    return None


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
                    pass  # Somebody has already restored the tag
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
                raise Exception('Members for {0} {1} were changed, cannot roll that back'.format(
                    obj['type'], obj['id']))
            else:
                obj['refs'] = change[1]
        else:
            raise Exception('Unknown or unprocessed by apply_diff change type: {0}'.format(
                change[0]))
    return obj


def print_changesets_for_user(user, limit=15):
    """Prints last 15 changesets for a user."""
    try:
        root = api_request('changesets', params={'closed': 'true', 'display_name': user})
        for changeset in root[:limit]:
            created_by = '???'
            comment = '<no comment>'
            for tag in changeset.findall('tag'):
                if tag.get('k') == 'created_by':
                    created_by = tag.get('v')
                elif tag.get('k') == 'comment':
                    comment = tag.get('v')
            logging.info(
                'Changeset %s created on %s with %s:\t%s',
                changeset.get('id'), changeset.get('created_at'), created_by, comment)
    except HTTPError as e:
        if e.code == 404:
            logging.error('No such user found.')
        else:
            raise


def print_status(changeset_id, obj_type=None, obj_id=None, count=None, total=None):
    if changeset_id == 'flush':
        sys.stderr.write('\n')
    elif changeset_id is not None:
        info_str = '\rDownloading changeset {0}'.format(changeset_id)
        if obj_type is None:
            sys.stderr.write(info_str)
        else:
            sys.stderr.write('{0}, historic version of {1} {2} [{3}/{4}]{5}'.format(
                info_str, obj_type, obj_id, count, total, ' ' * 15))
    else:
        info_str = '\rReverting changes'
        sys.stderr.write('{0}, downloading {1} {2} [{3}/{4}]{5}'.format(
            info_str, obj_type, obj_id, count, total, ' ' * 15))
    sys.stderr.flush()


def download_changesets(changeset_ids, print_status):
    """Downloads changesets and all their contents from API,
    returns (diffs, changeset_users) tuple."""
    ch_users = {}
    diffs = defaultdict(dict)
    for changeset_id in changeset_ids:
        print_status(changeset_id)
        root = api_request(
            'changeset/{0}/download'.format(changeset_id),
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
                    print_status(changeset_id, obj['type'], obj['id'], count, total)
                    try:
                        obj_prev = obj_to_dict(api_request('{0}/{1}/{2}'.format(
                            obj['type'], obj['id'], obj['version'] - 1))[0])
                    except HTTPError as e:
                        if e.code != 403:
                            raise
                        msg = ('\nCannot revert redactions, see version {0} at ' +
                               'https://openstreetmap.org/{1}/{2}/history')
                        raise RevertError(msg.format(obj['version'] - 1, obj['type'], obj['id']))
                else:
                    obj_prev = None
                diffs[(obj['type'], obj['id'])][obj['version']] = make_diff(obj, obj_prev)
        print_status('flush')
    return diffs, ch_users


def revert_changes(diffs, print_status):
    """Actually reverts changes in diffs dict. Returns a changes list for uploading to API."""
    # merge versions of same objects in diffs
    for k in diffs:
        diff = None
        for v in sorted(diffs[k].keys()):
            diff = merge_diffs(diff, diffs[k][v])
        diffs[k] = diff

    changes = []
    count = 0
    for kobj, change in diffs.items():
        count += 1
        if change is None:
            continue
        try:
            # Download the latest version of an object
            print_status(None, kobj[0], kobj[1], count, len(diffs))
            obj = obj_to_dict(api_request('{0}s?{0}s={1}'.format(kobj[0], kobj[1]))[0])

            # Apply the change
            obj_new = None
            if len(change) == 2 and change[1][0] == 'create':
                if not obj['deleted']:
                    obj_new = {'type': obj['type'], 'id': obj['id'], 'deleted': True}
            elif len(change) == 2 and change[1][0] == 'delete':
                # Restore only if the object is still absent
                if obj['deleted']:
                    obj_new = change[1][1]
                else:
                    # Controversial, but I've decided to replace the object
                    # with the old one in this case
                    obj_new = change[1][1]
            else:
                obj_new = apply_diff(change, deepcopy(obj))

            if obj_new is not None:
                obj_new['version'] = obj['version']
                if obj_new != obj:
                    changes.append(obj_new)
        except Exception as e:
            raise RevertError('\nFailed to download the latest version of {0} {1}: {2}'.format(
                kobj[0], kobj[1], e))
    print_status('flush')
    return changes


def main():
    if len(sys.argv) < 2:
        print('This script reverts simple OSM changesets. It will tell you if it fails.')
        print('Usage: {0} <changeset_id> [<changeset_id> ...] ["changeset comment"]'.format(
            sys.argv[0]))
        print('To list recent changesets by a user: {0} <user_name>'.format(sys.argv[0]))
        sys.exit(1)

    logging.basicConfig(level=logging.INFO, format='%(message)s')
    if len(sys.argv) == 2 and not sys.argv[1].isdigit():
        print_changesets_for_user(sys.argv[1])
        sys.exit(0)

    # Last argument might be a changeset comment
    ids = sys.argv[1:]
    comment = None
    if not ids[-1].isdigit():
        comment = ids[-1]
        ids.pop()
    changesets = [int(x) for x in ids]

    try:
        diffs, ch_users = download_changesets(changesets, print_status)
    except RevertError as e:
        sys.stderr.write(e.message + '\n')
        sys.exit(2)

    if not diffs:
        sys.stderr.write('No changes to revert.\n')
        sys.exit(0)

    try:
        changes = revert_changes(diffs, print_status)
    except RevertError as e:
        sys.stderr.write(e.message + '\n')
        sys.exit(3)

    if not changes:
        sys.stderr.write('No changes to upload.\n')
    elif sys.stdout.isatty():
        tags = {
            'created_by': 'simple_revert.py',
            'comment': comment or 'Reverting {0}'.format(', '.join(
                ['{0} by {1}'.format(str(x), ch_users[x]) for x in changesets]))
        }
        upload_changes(changes, tags)
    else:
        print(changes_to_osc(changes))
