# sample/word_tally

Counts the words of a text. Synthetic bundle used by the framework tests of
the standard profile; it is not part of the registry.

- Input: `text`, a string of at most 10,000 characters.
- Output: `words`, a list of `[word, count]` pairs sorted by word, and `total`.
- Errors: `INVALID_REQUEST` when `text` is missing or not a string,
  `TEXT_TOO_LONG` above the cap.
