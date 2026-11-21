# Changelog

All notable changes to this project are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and versions follow
[Semantic Versioning](https://semver.org/).

## [0.1.0] - unreleased

### Added

- `MeshLiteAvatarProcessor`: answers the page's `meshlite-session` RTVI request with a
  MeshLite client session (`audio: "track"`), and sends `meshlite-language` when the
  bot's TTS or STT language changes. The bot's audio passes through untouched.
- `MeshLiteClient`: creates MeshLite client sessions from a server.
- `to_meshlite_language()`: maps Pipecat languages to MeshLite lip models.
- One-file example (`examples/bot.py`) and `pipecat eval` scenarios.
- Tested with Pipecat 1.16.0.
