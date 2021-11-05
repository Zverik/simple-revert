# Reverting Scripts

Here are two Python scripts that:

* Revert simple OpenStreetMap changesets.
Way and relation structural changes cannot be reverted, all other changes can.
For example, if you deleted a lot of objects, or changed many tags at once.
* Restore an old version of given object(s). All of their deleted references
are restored as well.

If there are no errors, scripts would ask you for OSM login and password (which
are immediately forgotten) and upload the changes. Alternatively you can redirect
output to a file and open it in e.g. JOSM for reviewing and uploading.

## Installation

    pip install --user simple_revert

## Simple Revert

Specify changeset ids as the `simple-revert` script arguments. E.g.

    simple_revert 12345 12346 12348

To list recent changesets by a user (e.g. you), specify their name as
the only argument:

    simple_revert Zverik

## Restore Version

To restore an old object version, pass its type, id and version to
`restore_version`. For the first argument you can use following formats:

* `n12345` for a node, `w2342` or `r234234` for ways and relations.
* `node/12345`, `node.12345` or even `"way 2343"` (note the quotes).
* `https://www.openstreetmap.org/node/12345` or any other similar link.
* `https://api.openstreetmap.org/api/0.6/node/12345/6` includes a version to restore.

Version number should be either a positive integer, or a negative, relative
to the last version. E.g. this command will revert the last change to a node:

    restore_version n12345 -1

To get a list of recent versions, run the script without a version argument.

You can pass several objects to restore them all in one changeset:

    restore_version n12345 -1 w1234 -1 w1235 -1


## Author and License

Written by Ilya Zverev, licensed under ISC license.
