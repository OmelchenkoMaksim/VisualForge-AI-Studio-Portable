# Interface localization

Settings → Interface language switches between Russian and English immediately, without reloading
the page or restarting a model. Russian is the default. The choice is stored in the current browser
profile under `vf-language`; other studio windows in the same profile receive the storage event.
Blocked browser storage still permits changing the language for the current window.

Inputs, prompts, chat titles, model replies and reasoning, filenames, paths, command output and
original engine/OS diagnostics retain their contents. Language switching does not start a task,
answer a permission request, close a dialog or clear the selected files. This is an interface
preference, independent of speech recognition language, vocal language and model instructions.

## String files

`App/strings/` contains one JSON file per section: `image`, `video`, `music`, `text`, `agent`,
`speech`, `components`, `hardware`, `events`, `settings`, plus `common` for shared server messages.
Each key begins with its section, and every entry has both `ru` and `en` values. Keys describe the
location and purpose in English, for example `events.history_entry_failed_status` or
`text.chat_regenerate_response_button_label`. Use meaningful snake_case names with several words;
avoid hashes, counters and generic names such as `error` or `message`. Keep the key when editing
wording. The builder rejects opaque keys, names without enough context and duplicate JSON fields.

```json
{
  "image.generation_create_image_button_label": {"ru": "Создать изображение", "en": "Create image"}
}
```

Use numbered parameters (`{0}`, `{1}`, …), retaining the same parameters in both languages.
Parameters may move within a translation; their contents remain data. Older server messages
also use Python `%s` / `%d` / `%g` formatting, with matching specifiers in both translations.

Run `python -B tools/build_strings.py` after changing JSON. It validates keys, languages and
parameters and generates `App/frontend/strings.js`, a single synchronous offline bundle loaded
before interface scripts. `--check` verifies that the bundle is current. No translation service,
network request or model is used for localization.

## Presentation bindings

`App/frontend/i18n.js` owns the selected language and explicit text/attribute bindings. For example:

```javascript
StudioI18n.bind(button, 'textContent', () => StudioI18n.t('image.generation_create_image_button_label'), false);
```

Callbacks read presentation state only: never perform requests, clicks or task operations in them.
They run again on a language change. Use the final `false` argument for user data and keyed
templates; it disables compatibility translation of source messages. Input values and model text
are never subject to a blanket DOM translation pass. Bindings use weak references so detached
chat/history elements can be collected.

HTML text nodes have explicit `<!--i18n:section.key-->` markers. Translatable attributes use
`data-i18n-attrs`. Text is assigned as plain text, retaining the existing CSP and preventing translated
parameters from becoming markup. Native `<select>` controls keep ownership of arrow keys.

The compatibility renderer translates known application messages from backend metadata and stored
history. It does not translate captured filenames or arbitrary model/command output. Hardware
and model-requirement views refresh their presentation when locale-dependent numbers change;
generation tasks, permission cards and editable controls remain in place.

## Server messages

Bridge modules import `source` from `App/server/ui_strings.py` as `ui_text` and reference the same
section keys. Source messages intentionally remain Russian on the internal protocol and in stored
state, independently of the UI language of an individual window. The browser renders them in the
selected language. This avoids global locale changes affecting another request or persisted jobs.
Isolated inference workers and third-party diagnostic output keep their original log messages;
known application messages from those workers are covered by the compatibility catalogs.

```python
from ui_strings import source as ui_text
raise ValueError(ui_text('image.reader_image_read_error'))
```

## Verification

`python -B -m unittest -v tests.test_i18n` validates frontend/server key references, both languages,
parameter parity, the generated bundle and the production browser runtime against a small DOM
fixture. It covers immediate switching, persistence, cross-window events, blocked storage, dynamic
statuses, safe parameters, user text preservation and cleared bindings. The existing voice,
hardware, event-history and generation UI fixtures load the same production dictionaries.
