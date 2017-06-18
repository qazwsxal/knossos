#!/bin/bash

set -eo pipefail

cd /build
sudo chown packager .
rsync -a --exclude=dist --exclude=build --exclude=packer --exclude=.vagrant src/ work/
cd work

. releng/config/config.sh
import_key

export QT_SELECT=5
VERSION="$(python3 setup.py get_version)"

echo "Installing Babel..."
rm -rf node_modules
npm i babel-cli babel-preset-env

if [ -d dist ]; then
	rm -rf dist/*
fi

python3 configure.py
ninja dist

echo "Signing..."
for file in dist/*; do
	gpg -au "$GPG_KEY_ID" --detach-sign "$file"
done

echo "Uploading..."
twine upload dist/*