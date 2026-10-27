"""Deterministic offline prompt-based titles; never loads a language or vision model."""
import re
import time
import unicodedata
from pathlib import Path

RESERVED = {'CON','PRN','AUX','NUL',*(f'COM{i}' for i in range(1,10)),*(f'LPT{i}' for i in range(1,10))}

def clean_title(title):
    text = unicodedata.normalize('NFKD', str(title or '')).encode('ascii','ignore').decode()
    text = re.sub(r'[^A-Za-z0-9 -]+',' ',text)
    text = re.sub(r'\s+',' ',text).strip(' .-_')[:88].rstrip(' .-_')
    if text.upper() in RESERVED:text = 'Image ' + text
    return text

# Conservative vocabulary: unknown Cyrillic is discarded, never presented as English.
RU_TERMS = [
    (r'девуш\w*|женщин\w*', 'woman'), (r'мужчин\w*|парн\w*', 'man'),
    (r'ус(?:ы|ов|ами|а)?\b', 'mustache'), (r'бород\w*', 'beard'),
    (r'волос\w*', 'hair'), (r'фон\w*', 'background'), (r'освещени\w*|свет\w*', 'lighting'),
    (r'кинематографич\w*', 'cinematic'), (r'тепл\w*|тёпл\w*', 'warm'),
    (r'снеж\w*', 'snowy'), (r'горн\w*|гор(?:ы|ами|ах|у)\b', 'mountain'),
    (r'пейзаж\w*|ландшафт\w*', 'landscape'), (r'футуристич\w*', 'futuristic'),
    (r'город\w*', 'city'), (r'лет\w*', 'flying'), (r'машин\w*|автомобил\w*', 'vehicles'),
    (r'ноч\w*', 'night'), (r'красн\w*', 'red'), (r'син\w*|голуб\w*', 'blue'),
    (r'зелен\w*|зелён\w*', 'green'), (r'черн\w*|чёрн\w*', 'black'), (r'бел\w*', 'white'),
    (r'кот\w*|кошк\w*', 'cat'), (r'собак\w*', 'dog'), (r'портрет\w*', 'portrait'),
    (r'куртк\w*', 'jacket'), (r'плать\w*', 'dress'), (r'лиц\w*', 'face'),
    (r'улыб\w*', 'smile'), (r'детал\w*', 'details'), (r'резкост\w*', 'sharpness'),
]
OPERATIONS = [
 ('remove',r'\b(?:remove|erase|delete)\b|\b(?:убери|удали|убрать)\b'),
 ('replace',r'\b(?:replace|change|swap)\b|\b(?:замени|заменить|измени)\b'),
 ('recolor',r'\b(?:recolor|colour|color)\b|\b(?:перекрась|перекрасить)\b'),
 ('add',r'\b(?:add|insert|put)\b|\b(?:добавь|добавить|нарисуй)\b'),
 ('enhance',r'\b(?:enhance|improve|sharpen)\b|\b(?:улучши|улучшить)\b'),
 ('transform',r'\b(?:transform|turn|convert)\b|\b(?:преврати|преобразуй)\b'),
 ('upscale',r'\b(?:upscale|enlarge)\b|\b(?:увеличь|увеличить)\b'),
 ('generate',r'\b(?:generate|create|draw)\b|\b(?:создай|сгенерируй)\b')]

def infer_title(prompt, mode='single'):
    raw = str(prompt or '').strip().lower()
    # Preservation instructions should not dominate the creative action.
    raw = re.split(r'\b(?:preserve|keep|do not|don.t change)\b|\b(?:сохрани|не меняй|оставь)\b',raw,maxsplit=1)[0]
    operation = next((name for name,pattern in OPERATIONS if re.search(pattern,raw)),None)
    text=raw
    for pattern,translation in RU_TERMS:text=re.sub(pattern,translation,text)
    words=re.findall(r'[a-z]+',text)
    wordset=set(words)
    if 'mustache' in wordset:
        state='Removed' if operation=='remove' else 'Added' if operation=='add' else 'Edited'
        subject='Woman' if wordset & {'woman','girl'} else 'Man' if wordset & {'man','boy'} else None
        return f'{subject} With Added Mustache' if subject and state=='Added' else f'{subject} With Mustache Removed' if subject and state=='Removed' else f'Mustache {state}'
    if 'background' in wordset and operation=='replace':
        # Only use the replacement side, not the discarded background description.
        replacement=re.split(r'\b(?:with|to)\b|\bна\b',text,maxsplit=1)
        tail=replacement[-1] if len(replacement)>1 else text
        attrs=[w for w in re.findall(r'[a-z]+',tail) if w in {'snowy','mountain','forest','beach','city','white','black','blue','red','green','plain','studio','sunset','ocean'}]
        return ' '.join(dict.fromkeys(attrs+['background'])).title() if attrs else 'Background Replaced'
    if 'lighting' in wordset:
        attrs=[w for w in ['warm','cool','soft','dramatic','cinematic','natural'] if w in wordset or (w=='warm' and 'warmer' in wordset)]
        return (' '.join(attrs+['lighting'])).title() if attrs else 'Lighting Adjusted'
    excluded={'please','make','create','generate','draw','edit','change','add','insert','put','remove','delete','erase','replace','swap','improve','enhance','sharpen','transform','turn','convert','upscale','enlarge','recolor','the','a','an','to','of','and','at','in','on','from','into','this','image','picture','photo','more','looking','natural','s','it','is','as','by','for'}
    kept=[w for w in words if w not in excluded]
    if 'city' in kept and 'flying' in kept and ('vehicles' in kept or 'cars' in kept):kept=[w for w in kept if w!='night']
    while kept and kept[-1] in {'with','without'}:kept.pop()
    kept=kept[:9]
    suffix={'add':'Added','remove':'Removed','replace':'Changed','recolor':'Recolored','enhance':'Enhanced','transform':'Transformed','upscale':'Upscaled'}.get(operation)
    if kept:
        title=' '.join(kept).title()
        return clean_title(title+(' '+suffix if suffix else ''))
    return {'single':'Edited Image','reference':'Reference Edit','prompt':'Generated Image'}.get(mode,'Generated Image')

def build_filename_base(prompt, mode, submitted_at=None, *, operation='generate', upscale=1):
    """Use original user intent, then explicit operation markers; fallback cannot block saving."""
    try:title=clean_title(infer_title(prompt,mode))
    except Exception:title=''
    title=title or 'Generated Image'
    if operation=='variation':title += ' - Variant'
    if upscale in (2,4):title += f' - Upscale {upscale}x'
    stamp=time.strftime('%Y-%m-%d %H-%M-%S',time.localtime(time.time() if submitted_at is None else submitted_at))
    return title+' - '+stamp

def write_unique_output(directory, base, extension, raw):
    """Exclusive creation prevents concurrent jobs from overwriting a same-second result."""
    directory=Path(directory);directory.mkdir(parents=True,exist_ok=True)
    counter=1
    while True:
        path=directory/(base+('' if counter==1 else f'_{counter}')+extension)
        try:
            with path.open('xb') as stream:stream.write(raw)
            return path
        except FileExistsError:counter+=1
