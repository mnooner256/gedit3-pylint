#!/bin/bash -e

PLUGIN_DIR=$(readlink -f ~/.local/share/gedit/plugins/)
PLUGIN=$(readlink -f *.plugin)
PYTHON=$(readlink -f *.py)

#Remove old links in case we renamed the files
echo "Removing old links"
for f in $( find $PLUGIN_DIR -type l ) ; do
    printf '    '
    rm -v $f
done

printf "\nCreating links for our plugin"

printf '    '
ln -sfv "$PLUGIN" $PLUGIN_DIR
printf '    '
ln -sfv "$PYTHON" $PLUGIN_DIR
