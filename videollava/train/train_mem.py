#!/bin/
# Adopted from https://github.com/lm-sys/FastChat. Below is the original copyright:
# Adopted from tatsu-lab@stanford_alpaca. Below is the original copyright:
# Make it more memory efficient by monkey patching the LLaMA model with FlashAttn.
import sys
sys.path.append('/data/codes/lixiang/Video-LLaVA-main')
# Need to call this before importing transformers.
from videollava.train.llama_flash_attn_monkey_patch import replace_llama_attn_with_flash_attn

replace_llama_attn_with_flash_attn()

from videollava.train.train import train

import warnings

# Filter out warnings with a specific keyword or phrase
warnings.filterwarnings('ignore',
                        message='The default value of the antialias parameter')

if __name__ == "__main__":
    train()
