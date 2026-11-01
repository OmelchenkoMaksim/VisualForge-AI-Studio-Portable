# The Director: a whole film from a description and whatever materials you have

Added 2026-11-01, extended the same day with groups of materials, songs, voices and the soundtrack. In the Video tab the engine list ends with **"Режиссёр" (Director)**. It is not a model: it is a
producer that reads what the person attached, asks Gemma for a production plan, and then makes the film with the models the studio already has: pictures (FLUX.3 Klein), scenes with sound (LTX 2.5),
music (ACE-Step and the other music engines) and voices (the Voiceover module), joins the scenes with transitions and a colour grade, builds the soundtrack and registers the film. The result is an
ordinary entry of the Video history (player, export, mixer, deletion).

**Status.** Every part is covered by tests with a fake studio and the real FFmpeg (`tests/test_director_*.py`), and the page was checked in the studio's browser. See "What was measured" for what has and has not been run with the real models. Do not read this page as proof that films come out well.

## What the person does

1. Chooses "Режиссёр" in the engine list. The usual video form gives way to the Director's panel.
2. **Materials: everything is optional.** A description alone is enough; or fill every block. The panel offers one block for each group of materials, and **each block shows the files it has plus one empty
   slot** while it has room (add one, the next appears). Files can be dropped on a block, pasted, or chosen with the block's button. Every file can be given a short caption ("Алексей", "Главная песня")
   that the planner reads; that is how it matches a voice to a hero.

   | Block | Holds | Limit | Notes |
   |---|---|---|---|
   | Герои (heroes) | photos of people and characters | 10 | the cast: Gemma copies their descriptions into every scene they appear in |
   | Места и сцены (places) | photos of a city, a landscape, an interior | 20 | where the action happens |
   | Предметы (objects) | a car, a weapon, a thing | 10 | must appear as shown |
   | Видео-примеры (reference videos) | videos of any format | 8 | up to 10 minutes and 2 GB each; pace, palette and the kind of transitions are taken from them |
   | Свои песни (songs) | audio of any format | 6 | **up to an hour each**; the Director chooses excerpts and lays them under the scenes |
   | Диктор (narrator) | a voice sample | 3 | "Диктор", then "Диктор 2" (one may open the film, another close it), "Диктор 3" |
   | Главные герои и персонажи (characters) | a voice sample | 10 | "Главный герой", "Второй главный герой", then "Персонаж 3" ... "Персонаж 10" |

   A voice is a **sample to clone** (5-30 seconds of clean speech; the first 30 seconds after the silence are kept): the Director writes the lines and the speech engine says them in that voice. Uploading
   a voice needs the box **"I have the right to use these voices"** ticked (the Voiceover module asks the same); the server refuses a voice without it.
3. **The film**: length in seconds (5 to 300) and the number of scenes; both fields are always visible with a hint. **Level 0: below a minute a film has exactly 2 scenes.** From a minute the scenes may be
   `ceil(seconds / 60)` to `floor(5 x seconds / 60)`: at most 5 for every minute, and at least one (a scene lasts a minute at most, so a five-minute film cannot be two scenes).
   Format (landscape/portrait) and quality (draft/standard/maximum) may be left on "auto".

   **The "Ультра" (Ultra) switch** sits under these fields and is **on by default** (the choice is remembered in the browser). On means the best that the studio's models can make and hours of work: a minute
   of film can take 2-3 hours. Off is two to three times faster (about an hour for a minute). The switch decides what "auto" quality means and advises the planner; a choice made on the page (quality
   "draft", for instance) is always kept. What each setting is:

   | | Ultra on | Ultra off |
   |---|---|---|
   | Quality of the scenes, when nobody chose it | maximum (LTX 1280x704 in two passes) | standard (1024x576 in two passes) |
   | Keyframes (first frames made by Klein before a scene is shot) | **every scene**, one drawn from words alone when the scene shows no picture | only a scene that shows several pictures together |
   | Music engine, when the plan names none | the strongest that covers the film (MiniMax Music3 up to a minute, else ACE-Step) | the fastest (ACE-Step Turbo, then Stable Audio) |
   | What Gemma is told | write rich stills and full scene prompts, take the strongest music engine | keep prompts focused, take the fastest engine |
   | The plan screen | "Mode: Ultra (the best quality, slow)", the time in hours | "Mode: normal (faster)" |

   The picture model (Klein) has a single quality level, so in Ultra the pictures get **more keyframes and fuller instructions**, not more sampling steps; LTX's maximum size is the real cost (about 2.3 times the
   work of the standard one by the estimate: 70 against 30 seconds of work for a second of film).
4. **The advice on running time (хронометраж).** Under the length the panel says which running time suits the attached materials, why, and offers a button that takes it. Every place needs a scene (and more
   than two scenes need a minute: 6 places want 72 s), every voice needs a line (10 s a voice), and a song is best heard whole (a 3:12 song asks for 3:12; a longer one is played as an excerpt). With nothing attached
   any film of 10-60 s does well. The advice is a hint, not a rule: any length from 5 s to 5 minutes can be shot.
5. **The description** in free words: the story, the style of the reference videos, which photo is a character or a car, what the narrator says, where the song plays, the music. The voices speak in the language the description is written in (Cyrillic and Latin letters are counted), unless it asks for another.
6. **"Составить план"**: Gemma looks at the new pictures and videos and reads the numbers of the songs and voices (1-5 minutes, the first call loads the model from the disk). The plan is shown and can be
   edited: title, format, quality, the style phrase and the colour grade, the generated music, **the excerpts of the person's songs** (use, from which second of the song, at which second of the film, length, volume),
   and for every scene its length, its transition, its description in English and **its lines of speech** (who speaks, the words; lines can be removed and added).
7. **"Снимать фильм"** starts the shooting. The steps are listed with their state; **"Остановить съёмку"** (or the global Stop) ends it. Notes under the steps say what was left out.

While a film is shot the studio treats the card as busy (module `director` of the GPU coordinator): nothing else can start between two steps, and the banner says who works.

## What the Director does (`App/server/director/`)

| Step | Module | Detail |
|---|---|---|
| Limits, groups, scene rules, advice | `plan_rules.py` | `GROUPS` is the registry of materials (id, kind of file, limit); `scene_range`, `check_request`, `scene_job`, `estimate_minutes`, `advise` (the running time that suits the materials). The page takes the groups from `limits()` and repeats `scene_range`; `test_director_panel.py` runs the page formula in Node against it for every length |
| Materials | `media.py` | `Draft` (folder `UserData/Cache/Director/drafts/<id>`, pruned after 7 days, kept alive by a film). Photo to PNG; video to H.264 MP4; **song to AAC M4A** (an hour of lossless stereo is 600 MB, as AAC 86 MB, and FFmpeg cuts an excerpt by time without reading what comes before); **voice to a 24 kHz mono WAV** without leading silence, at most 30 s, refused when silent or shorter than 3 s. Facts: hard cuts and colours of a video; the average level and the **quiet / medium / loud stretches** of a song (per-second RMS from one FFmpeg pass, relative to the song itself). A thumbnail or a waveform for each |
| Looking and planning | `brain.py` (`DirectorBrain`, a `GemmaTask`) | `look`: captions of pictures (4 per call, told whether the person called each a hero, a place or an object) and videos (3 frames + facts), kept in `captions.json`. Songs and voices are not looked at: Gemma reads the song's length and stretches. `plan`: one JSON object; the rules for songs and for speech are added to the prompt only when there are songs and voices |
| Checking the plan | `plan.py` | `normalize` keeps only what the rules allow: the scene count, lengths scaled to add up to the film (2-60 s each), existing pictures, transitions, music engines, **song excerpts inside the song and the film** (crossfade where they meet, at most 8) and **lines of speech** (existing voices and scenes, at most 12 characters per second of the scene and 3 lines per scene, cut at the end of a sentence). Habits of Gemma seen in real plans are repaired here too: the quotes around a whole line are taken off (a voice is given the words, not the marks), a scene with several pictures and no keyframe gets one written from its pictures (twice Gemma wrote none for a hero, a place and a car in one scene, and the scene would have started from its first picture alone; in Ultra a scene with one picture or none gets one too). The keyframe text keeps Gemma's numbers of the draft ("picture 7"); they become places in Klein's list when it is asked (see "Rules that are easy to break"). The edited plan from the page passes the same checks before the film starts |
| The hands | `studio.py` (`Studio`) | Starts the studio's own jobs through the loopback bridge with the run token (`X-VisualForge-Run`): Klein keyframes (`/api/generate`), LTX scenes (`/api/video/generate`), music (`/api/music/generate`, audio from `/api/music/result`), **speech (`/api/voiceover/*`: the sample is registered as a voice for the length of the job, the lines are chapters of one text, the finished file says where each line begins, the voice and the job are deleted after)**. Registering the film and removing scene clips are calls into the Video service itself |
| The line | `runner.py` (`DirectorManager`) | Order: **speech**, keyframes, scenes, music, assembly, soundtrack, registration. Speech goes first so that a missing engine or a refused voice shows in the first minutes, not after an hour of shooting. A scene that fails is shot once more with another seed. **If a speech engine fails, the next installed one takes over** (VoxCPM2, then Qwen3-TTS; a note says so); a failed or stopped speech job is removed from the person's Voiceover history. At the start the film hard-links the pictures, voices and songs it uses into its own run folder, so removing a material from the page while the film is shot cannot break it. State in `UserData/Director/runs/<id>/run.json` (including `sound`: where every line and song was put and at which level); a restart marks an unfinished run interrupted |
| The film | `assemble.py` | One FFmpeg pass: each clip trimmed, scaled and cropped, `xfade`/`acrossfade` (0.6 s) or a hard cut, `eq`/`colorbalance` grade, levelling of the scenes' sound, fades, exact frame and sample counts |
| The sound | `soundtrack.py` | Three buses: the scenes' own sound, the music (excerpts of the person's songs and the generated track), the speech. Songs and tracks are first brought to one average level (-16 dB RMS), so that a cue's `gain_db` means the same for any song; the scenes' own sound is brought down to a bed (-26 dB RMS) under anything laid over it. **The music gives way to the speech (about 14 dB) and the scenes' sound gives way to the speech (about 6 dB)**, each a sidechain compressor listening to the louder bus. A line starts with its scene and never over the line before it; one that runs past the end is sped up by at most 1.3 times or left out (a note says so). **Final loudness:** a measuring pass of the same mix gives integrated LUFS; one constant gain towards -18 LUFS, bounded to -6…+14 dB, follows the ducking and precedes the limiter (0.8, about -1.94 dBFS, leaving AAC headroom). The gain does not change the balance the compressors set. Native-only films pass this stage too, without bed attenuation. The decoded AAC is measured again; LUFS before/after, gain and true peaks are kept in `sound.mix.loudness` of the run's `run.json`. Silence gets no boost and retains the existing rejection of inaudible mixes. The picture is copied, not encoded again; the result is checked (AAC 48 kHz stereo, the length of the film, not silent, decoded true peak no higher than 0 dBFS). Bounds and peak limiting mean that -18 LUFS is a target, not a promise |
| Routes | `routes.py` | `GET` of `draft`, `status` and `media`; `POST` of `attach` (raw bytes, `group=`), `detach`, `label`, `plan`, `start` and `cancel`; all under `/api/director/`. `media` plays a song or a sample back by byte ranges |

A scene is **one continuous take**: up to 5 s in one LTX generation (49/97/121 frames), longer scenes continue the same take in windows of 15, 30 or 60 s and are trimmed to the planned length. Every
clip is generated as long as its scene plus the 0.6 s that the transition into the next scene eats, so the film keeps exactly the planned length. A scene starts from a keyframe (Klein, composed from
the scene's pictures when the plan asks for it and ComfyUI is ready), else from its first attached picture, else from text alone.

Quality presets: **draft** 768x512 in one pass, **standard** 1024x576 in two passes, **maximum** 1280x704 in two passes (portrait: the same sizes turned). Music: ACE-Step (up to 180 s, song or
instrumental), MiniMax Music3 (60 s), Stable Audio 3 (120 s, no singing); a film longer than the track loops it, and the plan may start the track later in the film (after a song of the person).
Speech: VoxCPM2 when installed (48 kHz), else Qwen3-TTS; the Voiceover module also listens to what it made and repeats a line that came out wrong (lines still doubtful are counted in a note).

The film is copied to the Video save folder as `<title> - <time> - Director.mp4` and appears in the history as a normal clip. The scene clips are removed from the history when the film is registered (they are only
material). The keyframes remain in the Image history: they are real Image generations. The run's folder keeps its keyframes and its log; the films, the track and the cut lines are removed.

## Rules that are easy to break

* The Director never calls the managers directly; it asks the routes, so the disabled-component checks, the GPU claim, the naming and the history all apply. Its requests carry the run token and the bridge
  wraps them in `GpuCoordinator.acting_for('director')`; anything else is refused while a film is shot. A new kind of step must go through a route the same way.
* The film is the Video service's business once it exists: `Jobs.register_film` accepts only an MP4 inside `UserData/Director` and is reachable only from the bridge process (it is not a page route).
* Scene counts and lengths are rules, not hints: `check_request` runs on the page's numbers again at planning and at start.
* Gemma never decides the numbers of the film: it proposes, `normalize` disposes. It never names a file: songs, voices and pictures are numbers of a draft, checked against the draft.
* A new group of materials is one row in `plan_rules.GROUPS` plus its strings (`director.group_<id>_title|hint|add`, `director.slot_<id>_first|second|other`); the panel builds its blocks from the list the server
  sends. A new kind of file (not photo, video, audio or voice) also needs a reader in `media.py`. `test_director_panel.py` fails with the name of a missing string.
* Uploaded files are not trusted: ffprobe must name a known container (a playlist, a concat script or a network description that merely points elsewhere is refused), only the `file` protocol is allowed, and the
  copy the Director keeps is the one it converted itself.
* **FFmpeg's `sidechaincompress` threshold is not a level in dBFS.** A key at -14 dB RMS needs `threshold=0.002` to push the music down 19 dB; the value the Video mixer uses (0.04) hardly moves it below -8 dB.
  The three profiles in `soundtrack.py` were measured with sine keys of known level (their table is in the comment above them). The mixer's own profile is not used for the Director.
* A voice is cloned only for a person who said they may use it. The consent box is not decoration: the route refuses a voice upload without `consent=1`, and the Voiceover job registers the sample with consent only for that run.
* **Klein draws on the shape of the first picture it is given** (the canvas it is asked for counts only when there is no picture), and the video model takes a middle band out of a start picture that has not
  the shape of the film. `media.frame_pictures` puts the pictures of a scene that have the film's shape first and, when none has it, puts the first one on a canvas of the film's shape over a blurred copy of itself
  and tells the model to continue the scene into the blur (it keeps the person's clothes and fills the sides with the scenery). The plan keeps the numbers of the draft in the keyframe text ("picture 4" is the
  picture shown as `#4`); they become places in the model's list once, at the moment it is asked (`plan.renumber_pictures`, called by the runner and never by `normalize`: a plan is normalized again when the film
  starts, and renumbering text that was already renumbered scrambled the words of all four keyframes of the film of a minute). A scene that starts from its one attached picture without a keyframe (normal mode)
  still gets the video model's own centre crop: a tall photo in a wide film shows its middle band.
* What "Ultra" means is `PROFILES` in `plan_rules.py` and nowhere else: the planner is told in words (`ULTRA_RULES` / `NORMAL_RULES` in `brain.py`), and `normalize` applies the profile when the answer does not follow
  the words (the quality nobody chose, a keyframe for every scene, the music engine). The flag has to be a real `true`: any other shape from a page or a script is the normal profile. A film is shot in the mode its plan was
  written for (the page sends the plan's mode at start, not the switch as it stands later), because a plan made for Ultra has a keyframe for every scene and one made for normal has not.
* The page polls the run every two seconds and rebuilds the "Shooting" box only when something it shows has changed (`renderRun` compares a signature; the bar moves in place). A box rebuilt on every poll
  takes the keyboard focus away from the Stop button and drops a click that begins before and ends after the rebuild (seen in the browser tests: the reference of the Stop button was stale two seconds after it was found).
* The style's saturation runs from 0 to 1.4 and the planner is told that 0 is black and white. With a floor of 0.7 a film asked for in black and white came out in full colour: the grade could not take
  the colour out, and the scenes started from a colour picture whatever the style phrase said.

## What was measured

* **With fakes and the real FFmpeg (tests):** the order of the steps, retries, stops and failures; every cue of the soundtrack placed in time and measured by the level of its own tone in the finished file (the music
  falls 8+ dB under a voice and recovers in half a second; the scenes' sound falls 3+ dB; an excerpt starts where it is told and fades); song and voice conversion; the group limits; a playlist that points at another file.
* **Final loudness stage (2026-11-01, CPU and bundled FFmpeg, no model/GPU):** the old `mixdown` left a quiet synthetic film at -33.1 LUFS; the same probe now reaches -19.1 LUFS with the gain capped at +14 dB
  (decoded true peak -18.56 dBFS). Real-FFmpeg regression tests independently scan the decoded AAC with R128: a quiet mix within the gain budget reaches -18 LUFS within 1.5 LU; an input too quiet to reach it stays
  at the +14 dB cap; a deliberately overdriven mix takes -6 dB and stays below full scale after limiting and AAC. The music-to-voice ratio is compared against the unmastered mix within 0.5 dB, in addition to the
  original sine-key ducking checks. Their absolute noise-floor comparisons subtract only the common final gain. Exact silence gets no gain and is not published. The native-only production test reads the before/after
  values back from `run.json`. These are synthetic audio checks, not a new film generation or a listening judgement; the real-film numbers below predate this stage.
* **Already rendered Ultra film, audio-only remaster (2026-11-01, CPU):** the quiet 10-second film below was copied through the new stage into C user-temp, without reshooting or changing the original.
  Its finished mix measured -26.805 LUFS before and -19.934 LUFS after a +8.805 dB make-up gain; the decoded AAC true peak after mastering was -1.766 dBFS. Peak limiting kept this high-crest-factor material below
  the -18 LUFS target. Evidence: `vfr-tests/director-loudness-probe/ultra-audio-mastered.json` and its MP4 in the Windows user's Temp. This checks the final stage on existing audio, not a fresh end-to-end film.
* **With the real models (2026-11-01, RTX 5070 Ti, models on the hard disk):**
  * A film of 10 s in two scenes, draft quality, with a picture of a hero, a place and a car, a narrator's and a character's voice (a recitation sample of 30 s and one of the built-in voices) and one
    song of the person: Gemma's plan 3-4 minutes (cold, three pictures looked at); both voices 100 s (VoxCPM2, every line checked by Whisper); the video model's load and the first scene 6.5 minutes,
    the second scene 2; assembly and soundtrack 15 s; **10.6 minutes in all**. The film is exactly 240 frames and 10.000 s, AAC 48 kHz stereo, -19.7 LUFS, peak -3.3 dBFS. The studio's own Whisper
    transcribed the finished mix as both lines word for word, so the voices are understood over the song and the scenes' sound. The same film kept going while the person removed the song, a voice and
    the picture from the draft (the film's own links). The two scenes show the red car from the photo of the object and the woman of the hero's photo in her hat and scarf, joined by a dissolve.
  * What the first attempts found: VoxCPM2's first compilation died of the Cyrillic install path in `TRITON_CACHE_DIR` (fixed in the Voiceover and Music managers, and the worker falls back to
    no compilation); Gemma wrote the generated music in fields of its own (genre, mood...) and the track vanished (the shape is spelled out now and the fields are put together); the scenes'
    own sound was ~-10 dB RMS and drowned a song (now brought to -26 dB under anything laid over it). `UserData/Logs/Director/plans.log` keeps what Gemma answered, for the next such case.
  * A film from the description alone (no materials at all): "a lighthouse in a storm, anxious orchestral music without vocals", 10 s, draft. Gemma's second plan carried the track as
    `minimax-music3-int8`, instrumental (the first answer had spelled it as genre / mood / instruments and was dropped: see above); the plan took about a minute (Gemma already loaded); the track, both scenes,
    assembly and soundtrack took **9.2 minutes in all**. The film is 240 frames and 10.000 s, AAC 48 kHz stereo, -21.1 LUFS, peak -9.1 dBFS: the track sits under the waves of the scenes (its bed was lifted 3.6 dB to the reference
    level and then lowered to the plan's -16 dB, so about -32 dB RMS against the scenes' -26) and fades out over the last seconds. Quiet, as the plan asked for "unobtrusive": a person who wants the
    music in front writes so in the description, or raises its gain in the plan.
  * Two narrators: "Narrator" opens the film in scene 1 and "Narrator 2" closes it in scene 2 (the two built-in announcer voices, one picture of a harbour, 10 s, draft). The page showed both slots and the
    third one open; the plan gave one line to each. Both voices took 4 minutes with the cold model load, the first scene 6 minutes (cold video model), the second 1.5: **11.4 minutes in all**. The film is
    240 frames and 10.000 s, -20.2 LUFS, peak -7.2 dBFS; the studio's Whisper wrote both lines word for word, and the pitch of the two lines in the finished file (median 206 and 155 Hz) shows two voices.
    The scenes' own sound was faint (-34.7 dB RMS) and was lifted by the limit of 6 dB.
  * A black-and-white film in portrait at standard quality (10 s, one narrator, the same harbour): the plan 4 minutes (Gemma cold), the voice 2 minutes, scene 1 in four continuation windows and scene 2:
    **13.7 minutes in all**. The file is 576x1024, 240 frames, 10.000 s, -16.8 LUFS, peak -2.3 dBFS; the mean saturation of its frames is 0.96 (FFmpeg's `signalstats`, 0 is pure grey) and the line was
    heard word for word. The same request before the fix had come out in colour (see "Rules that are easy to break").
  * Stop: pressed while the first scene of the film above was being shot (the video model loading), through the route. The run was `cancelled` 8 s later, the ComfyUI queue was empty, the video card's
    memory was down to 0.7 GB twenty seconds later and the Voiceover module was idle; the page showed the plan again with "Shoot the film" enabled and "Stop the shooting" disabled.
  * **A film of a minute in four scenes of 15 s** (draft, landscape) made of everything the Director takes: two hero photos (a woman and a captain), two places (a harbour and a desert), a red car, one
    song of 3:09 (an excerpt of 60 s lies under the film), a narrator and a character voice, and a generated track. Gemma wrote the first plan with no keyframe at all; the next ones had keyframes for some
    scenes, and `normalize` wrote the rest from the pictures of the scene (see "Checking the plan"). The run: both voices 4.3 minutes (cold model), four keyframes by Klein 4.7 (the first with Klein's cold
    load), the scenes 10, 6, 3.5 and 3.3 minutes (each in eight continuations of the same take; the first with the cold load of the video model: about 14 s of work for each second of film), the track
    (ACE-Step) 73 s, assembly and soundtrack 10 s: **35.8 minutes in all** (the plan screen had said "about 30"). The file is 60.000 s, 1440 frames, AAC 48 kHz stereo, -22.4 LUFS, peak -5.3 dBFS. The
    narrator (1.0 s) and the character (16.0 s) were written by the studio's Whisper word for word over the song, the generated track and the scenes' sound. The woman in the hat and scarf and the
    captain with the white beard are the same people in the four scenes, and the harbour, the desert and the red car look like their photos: the keyframes composed them from the photos.
    Gemma had also asked for a generated track over the person's song (the page shows it ticked and the person may untick it); the rules for songs now tell it to leave "music" empty when the songs cover the film.
  * A reference video (the film above, one minute: the intake took 2 s and kept 9 MB of 25): the plan for "a film in the style of my example" took its palette (warmth 0.8, contrast 1.2,
    saturation 0.6, "deep oranges and earthy browns") and its transitions. Gemma still laid the person's song under the film although the idea said "no music": the plan page shows the song ticked and the
    person unticks it. Only the plan was written; the film was not shot.
  * **The Ultra switch, planned for real (2026-11-01, only the plans were written, no film was shot).** The same description (Anna and the captain, a narrator and a character, a song, the pictures of the
    film above; 60 s in four scenes) planned twice by the real Gemma: with Ultra off the plan said "standard", no keyframes of its own and no generated track (`normalize` wrote keyframes for the four scenes
    because each shows several pictures), the page said "Mode: normal" and "about 48 min"; with Ultra on it said "max", **a rich keyframe for every scene written by Gemma itself**, no generated track over the
    person's song (the rule for songs works), "Mode: Ultra" and "about 88 min". A film of two minutes is estimated at 2 h 38 min and shown in hours. The switch kept its state across a reload, is on on a first
    visit, switches with a click on its label, and the Russian and English texts change at once.
  * **An Ultra film of 10 s (2026-11-01).** A photo of a hero and a photo of a harbour, "Anna walks along the old harbour at dawn, then the camera comes close to her face, quiet warm music without
    vocals", Ultra on, frame and quality on auto. Gemma wrote quality max, a keyframe for each of the two scenes (6 s and 4 s) and an instrumental track by Stable Audio (not the first engine of the Ultra
    order: a valid choice of Gemma is kept). The plan screen said "about 24 min"; the film took **18.5 minutes**: both keyframes 3.4 minutes (Klein loaded cold), scene 1 (6.6 s with the transition, shot in
    four continuations) 10.4 minutes with the cold load of the video model, scene 2 (4 s, one shot) 3.4, the track 73 s, assembly and sound 4 s. The file is 1280x704, 240 frames, 10.000 s, AAC 48 kHz stereo.
    That makes about 67 s of work for a second of film at maximum quality for a scene in continuations and about 51 for a single shot (the estimate's 70 is a little high, which is the safe side); with the
    fixed costs of the loads, a minute of Ultra film comes to about 80 minutes by this one measurement, not the 2-3 hours the owner expected. The sound was quiet (-26.8 LUFS: the scenes' own sound was -38 dB RMS
    and the track sits at -32) with short peaks (-0.6 dBFS in the file): this measurement predates the final loudness stage above.
    **The picture was wrong** and the film showed why a keyframe for every scene needs care: Klein draws on the proportions of the first picture it is given, so both keyframes came out portrait (832x1248)
    from the portrait photo of the hero, and the video model cut a middle band out of each: scene 1 began at the woman's torso with her head out of the frame, scene 2 showed the lower half of her face. The
    framing described under "Rules that are easy to break" fixes it: the same two keyframes made again (not shot into a film) are a wide shot of the whole harbour (1248x832, the photo of the place first) and a
    landscape close-up (1376x752) in which the woman wears the same hat, scarf and jacket.
  * Not yet run for real: films longer than a minute (up to five scenes for every minute), and an Ultra film again with the framing in place.

Estimates on the plan screen come from the studio's earlier measurements (seconds of film times 12, 30 or 70 for draft, standard, maximum, plus the loads of the keyframe, music and speech models) and are rough.

## Limits and what comes next

* Scenes are made by LTX only. Wan Animate 2 and SCAIL-2 (a photo repeating a reference video) and Wan S2V (a talking face) are the next producers. **Voices are heard over the picture with no lip sync**: the planner
  is told not to put the words of an assigned voice into the scene prompt (the person is shown from behind, in profile, far away). Lip sync would need S2V per line.
* Gemma cannot hear: a song is described to it by its length and its quiet / medium / loud stretches (and the person's caption), not by its lyrics or its genre. Say in the description what the song is.
* The reference videos give pace, palette and the kind of transitions to Gemma as measured numbers and a few frames: it cannot see a transition, only the cuts around it.
* A failed film is not resumed; the run's folder keeps its keyframes and the log of its steps.
* Level 0 is two scenes below a minute; the code for the higher levels is the same and equally untested with the real models.

Tests: `tests/test_director_rules.py`, `test_director_media.py`, `test_director_brain.py`, `test_director_assemble.py`, `test_director_soundtrack.py`, `test_director_runner.py`, `test_director_studio.py`,
`test_director_register.py`, `test_director_routes.py`, `test_director_panel.py` (the real FFmpeg, no model, no GPU), and `test_gpu.py` for the coordinator.
