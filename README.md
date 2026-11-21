# Oval Survivor

A survival-focused bot for the Mech Royale hackathon. It stays inside the
shrinking zone, uses evasive movement, faces nearby threats, picks useful
weapons, and fights selectively.

The bot is implemented in [`bot.py`](bot.py) and uses the starter kit's
standard-library protocol wrapper in [`mechfight.py`](mechfight.py). The
container image is defined by the top-level [`Dockerfile`](Dockerfile).

## Requirements

- Python 3.9 or newer for running the bot and native conformance check.
- Docker for building and checking the competition-style image.

The image uses Python 3.13.0 and requires no third-party packages.

## Test

The starter kit archive contains the protocol checker, recorded observations,
schema, and arena map. Extract it from the repository root:

```sh
tar -xzf mechfight-kit.tar.gz
```

Run the protocol check directly:

```sh
python3 -m unittest discover -s tests -v
python3 mechfight-kit/check/check.py -- python3 -B -u bot.py
```

Build the image for the competition's Linux AMD64 target:

```sh
docker build --platform linux/amd64 -t oval-survivor .
```

Run the checker against the image with the match resource and filesystem
limits:

```sh
python3 mechfight-kit/check/check.py --map-dir /arena -- docker run --rm -i \
  --network none --cpus 1 --memory 512m --read-only \
  --tmpfs /tmp:rw,size=64m --user 65534:65534 \
  -v "$PWD/mechfight-kit/arena:/arena:ro" oval-survivor
```

The conformance check verifies protocol behavior and response timing; it does
not simulate matches or predict a leaderboard score.
