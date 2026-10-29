"""Private Qwen voice dependencies shadow Video packages in this child only."""
import argparse
import json
import os
from pathlib import Path
import subprocess
import sys

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--packages', type=Path, required=True)
    parser.add_argument('--request', type=Path, required=True)
    parser.add_argument('--ffmpeg', required=True)
    args = parser.parse_args()
    sys.path.insert(0, str(args.packages))
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from generation_identity import operation
    data = json.loads(args.request.read_text(encoding='utf-8'))
    reference = args.request.with_suffix('.reference.wav')
    subprocess.run([args.ffmpeg, '-nostdin', '-v', 'error', '-n', '-i', data['voice_path'], '-t', '30', '-ac', '1', '-ar', '24000', str(reference)], check=True)
    import torch
    import soundfile as sf
    from qwen_tts import Qwen3TTSModel
    torch.manual_seed(20261024)
    model = Qwen3TTSModel.from_pretrained(data['model'], device_map='cuda:0', dtype=torch.bfloat16, attn_implementation='sdpa', local_files_only=True)
    with operation('SpeechSynthesis'):
        wavs, rate = model.generate_voice_clone(text=data['speech_text'], language=data.get('language', 'Russian'), ref_audio=str(reference), x_vector_only_mode=True, max_new_tokens=2048)
    sf.write(data['output'], wavs[0], rate)
    print(json.dumps({'event': 'voice_done', 'samples': len(wavs[0]), 'sample_rate': rate, 'model': 'Qwen3-TTS-12Hz-0.6B-Base'}), flush=True)

if __name__ == '__main__':
    main()
