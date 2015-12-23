#!/bin/sh

# Load OpenStack credentials
if [ -f "/etc/os.conf" ]; then
    . /etc/os.conf
fi

set -e
set -x

# Load our deployment config
. /etc/mason.conf

if [ ! -e ws ]; then
    morph init ws
fi
cd ws

definitions_repo="$DEFINITIONS_REF"/"$UPSTREAM_TROVE_ADDRESS"/baserock/baserock/definitions
if [ ! -e "$definitions_repo" ]; then
    morph checkout git://"$UPSTREAM_TROVE_ADDRESS"/baserock/baserock/definitions "$DEFINITIONS_REF"
    cd "$definitions_repo"
    git config user.name "$TROVE_ID"-mason
    git config user.email "$TROVE_ID"-mason@$(hostname)
else
    cd "$definitions_repo"
    SHA1_PREV="$(git rev-parse HEAD)"
fi

if ! git remote update origin; then
    echo ERROR: Unable to contact trove
    exit 42
fi
git clean -fxd
git reset --hard origin/"$DEFINITIONS_REF"

SHA1="$(git rev-parse HEAD)"

if [ -f "$HOME/success" ] && [ "$SHA1" = "$SHA1_PREV" ]; then
    echo INFO: No changes to "$DEFINITIONS_REF", nothing to do
    exit 33
fi

rm -f "$HOME/success"

echo INFO: Mason building: $DEFINITIONS_REF at $SHA1

if ! "scripts/release-build" --no-default-configs \
        --trove-host "$UPSTREAM_TROVE_ADDRESS" \
        --artifact-cache-server "http://$ARTIFACT_CACHE_SERVER:8080/" \
        --controllers "$DISTBUILD_ARCH:$DISTBUILD_CONTROLLER_ADDRESS" \
        "$BUILD_CLUSTER_MORPHOLOGY"; then
    echo ERROR: Failed to build release images
    echo Build logs for chunks:
    find build-* -type f -exec echo {} \; -exec cat {} \;
    exit 1
fi

releases_made="$(cd release && ls | wc -l)"
if [ "$releases_made" = 0 ]; then
    echo ERROR: No release images created
    exit 1
else
    echo INFO: Created "$releases_made" release images
fi

if [ "$TEST_INFRASTRUCTURE_TYPE" = "openstack" ]; then
    "scripts/release-test-os" \
        --deployment-host "$DISTBUILD_ARCH":"$MASON_TEST_HOST" \
        --trove-host "$UPSTREAM_TROVE_ADDRESS" \
        --trove-id "$TROVE_ID" \
        --net-id "$OPENSTACK_NETWORK_ID" \
        "$BUILD_CLUSTER_MORPHOLOGY"
elif [ "$TEST_INFRASTRUCTURE_TYPE" = "kvmhost" ]; then
    "scripts/release-test" \
        --deployment-host "$DISTBUILD_ARCH":"$MASON_TEST_HOST" \
        --trove-host "$UPSTREAM_TROVE_ADDRESS" \
        --trove-id "$TROVE_ID" \
        "$BUILD_CLUSTER_MORPHOLOGY"
fi

"scripts/release-upload" --build-trove-host "$ARTIFACT_CACHE_SERVER" \
	--arch "$DISTBUILD_ARCH" \
	--log-level=debug --log="$HOME"/release-upload.log \
	--public-trove-host "$UPSTREAM_TROVE_ADDRESS" \
	--public-trove-username root \
	--public-trove-artifact-dir /home/cache/artifacts \
	--no-upload-release-artifacts \
	"$BUILD_CLUSTER_MORPHOLOGY"

echo INFO: Artifact upload complete for $DEFINITIONS_REF at $SHA1

touch "$HOME/success"
