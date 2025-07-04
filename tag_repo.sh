#!/bin/bash

# Read version from pyproject.toml
VERSION=$(grep '^version = ' pyproject.toml | cut -d'"' -f2)

if [ -z "$VERSION" ]; then
    echo "Error: Could not find version in pyproject.toml"
    exit 1
fi

TAG="v$VERSION"

echo "Creating tag $TAG"
git tag "$TAG"
git push origin "$TAG"

git tag -l