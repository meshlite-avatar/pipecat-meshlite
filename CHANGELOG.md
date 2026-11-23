# Changelog

All notable changes to this project are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and versions follow
[Semantic Versioning](https://semver.org/).

## [0.1.0] - unreleased

### Added

- `MeshLiteAvatarProcessor`: answers the page's `meshlite-session` RTVI request with a
  MeshLite client session (`audio: "track"`), at most `max_sessions` requests per
  connection, refusals included. It sends `meshlite-language` when the language the bot
  speaks changes, read from the text the TTS pushes (`set_language()` for a deliberate
  switch). The bot's audio passes through untouched.
- `MeshLiteClient`: creates MeshLite client sessions from a server.
- `to_meshlite_language()`: maps Pipecat languages to MeshLite lip models;
  `spoken_language_of()`: the language of a text, same rule as the MeshLite web SDK.
- One-file example (`examples/bot.py`) and `pipecat eval` scenarios.
- Tested with Pipecat 1.16.0 and 1.15.0.
