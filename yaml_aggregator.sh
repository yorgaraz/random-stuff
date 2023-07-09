#!/bin/bash
if [ -z "$1" ]
then
    echo "Please provide a directory."
    exit 1
fi
indir="$1"
shouldPrependFileLocation=false
if [ "$2" == "-f" ]
then
    shouldPrependFileLocation=true
fi
find $indir -name "*.yaml" -type f | while read file
do
    if [ "$shouldPrependFileLocation" = true ] ; then
        echo "\"$file\":" >> /dev/stdout
        sed -e 's/^/  /' "$file" >> /dev/stdout
    else 
        cat "$file" >> /dev/stdout
    fi
    echo "" >> /dev/stdout
    echo "---" >> /dev/stdout
done
