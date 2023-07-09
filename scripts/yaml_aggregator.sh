#!/bin/bash

if [ "$1" == "-h" ]
then
    echo "yaml_aggregator.sh: Aggregates all yaml files in a directory into a single yaml file and outputs to stdout."
    echo "Usage: yaml_aggregator.sh <directory> [-f]"
    echo "  -f: Prepend the file location to each yaml file as the root key."
    echo "Example: yaml_aggregator.sh ./random_gitops_cluster_stuff -f > ./all.yaml"
    exit 0
fi

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
