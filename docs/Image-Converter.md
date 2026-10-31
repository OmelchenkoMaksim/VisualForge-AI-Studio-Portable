# Picture converter: any format in, what the neural network reads out

Added 2026-10-31. A person may choose, paste (a screenshot, Ctrl+V) or attach a picture of any common format in Image, Video and the Text chat. The studio converts it with
**Pillow**, the open-source imaging library that is already in every runtime, instead of refusing it. Nothing leaves the computer.

## What happens to a picture

1. The format is read from the file itself (name and declared type are not trusted).
2. A picture that is already acceptable for the consumer passes **untouched**. Otherwise it is converted: the first frame of an animation, EXIF rotation applied, the colour profile converted to sRGB,
   CMYK / palette / grey / 16-bit modes turned into RGB(A), transparency kept (Image) or flattened on grey (Video), a long side above the limit scaled down, and the file re-encoded smaller when it is over the byte limit
   (a PNG becomes a JPEG only for the chat).
3. A file nobody can decode (damaged, truncated, a bomb above 250 megapixels) is refused with a message that says so; a HEIC file gets its own hint.

| Consumer (`profile`) | Passes untouched | Output otherwise | Limits |
|---|---|---|---|
| `video` (LTX, Wan, S2V, Animate 2, SCAIL-2, scene keyframes) | PNG in RGB | PNG | 8192 px a side, 36 MiB, no alpha |
| `image` (all Image modes, masks, references) | PNG, JPEG, WebP in RGB (PNG also RGBA) | PNG | 8192 px a side, 100 MiB |
| `chat` (vision models of the Text chat) | PNG, JPEG | PNG, JPEG when over 6 MiB | 2560 px a side, 6 MiB |

Formats read by Pillow 12 in all three runtimes: PNG, JPEG, JPEG 2000, WebP, **AVIF**, GIF, BMP/DIB, TIFF, ICO/CUR, ICNS, TGA, DDS, QOI, PSD, PCX, PPM/PGM/PBM, SGI, EPS, FITS and more.
**HEIC/HEIF** (iPhone) is read through `pillow-heif` 1.8.1 (component `image.heic`, a 6.3 MiB wheel from PyPI with a pinned SHA-256, installed with the programs into the Image engine's Python 3.13; the wheel is GPL-2.0 as a whole because it bundles libheif, libde265 and x265, see `THIRD-PARTY-NOTICES.md`). Without it a HEIC file is refused with the hint to save it as JPEG.

## Where it is wired in

* `App/server/image_convert.py`: `convert(raw, profile)` -> `Result(data, format, width, height, unchanged, source, changes)`; `tests/test_image_convert.py`.
* `video_service.Jobs.image()` (every frame of every video request, also each scene keyframe), `studio_server.check_image()` (every picture the Image section sends to ComfyUI),
  `text/chat_store.decode_image()` (pictures of the chat), and the route **`POST /api/image/convert?profile=video|image|chat`** (raw bytes in, JSON with `base64`, `mime`, size and the list of `changes` out; CPU only).
* The browser: `App/frontend/studio-images.js` (`StudioImages`). Each panel first lets the browser decode the file (fast, local); when the browser cannot (TIFF, PSD, HEIC...), the file is sent to
  `/api/image/convert` and the PNG that comes back is used. The file dialogs offer the extra extensions (`.heic`, `.tif`, `.psd`, `.tga`, `.avif`...), pasting a screenshot works in the Image section, the chat and the character cards.
  Limits of the old panels were raised from 36/40 MiB to 100 MiB because the converter shrinks the picture.

Checked on 2026-10-31: AVIF, TIFF, BMP, WebP, GIF and TGA through `/api/image/convert` (all PNG, 300x200); a TIFF file chosen in the Video panel (Chromium cannot show TIFF) arrived as a 60x40 PNG preview;
garbage bytes returned the readable message. Not tested: a photo from a real iPhone (a HEIC made by the package itself was converted), PSD with layers, camera RAW (not supported: needs a separate decoder).
