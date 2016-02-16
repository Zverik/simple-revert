# Simple Revert

This is a single Python script that reverts simple OpenStreetMap changesets.
Way and relation structural changes cannot be reverted, all other changes can.
For example, if you deleted a lot of objects, or changed many tags at once.

## Installation

    git clone https://github.com/Zverik/simple-revert.git
    sudo pip install lxml

## Usage

Specify changeset ids as the script arguments. E.g.

    ./simple-revert.py 12345 12346 12348

If there are not errors, it will ask you for OSM login and password (which
are immediately forgotten) and upload the changes.

## Author and License

Written by Ilya Zverev, licensed WTFPL.
