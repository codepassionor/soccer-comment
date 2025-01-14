#!/bin/bash
HOME_PATH='/data/codes/lixiang/Video-LLaVA-main'
JSON_FOLDER="/data/codes/lixiang/Video-LLaVA-main/dataset/soccernet_json"
#IMAGE_FOLDER= None
#VIDEO_FOLDER="/data/lx/Video-LLaVA-main/dataset"
# --audio_tower /data/codes/lixiang/Video-LLaVA-main/tower/LanguageBind_Audio \
VIDEO_FOLDER="/data/codes/lixiang/soccernet/caption_anno_clips"
cd /data/codes/lixiang/Video-LLaVA-main

HF_DATASETS_OFFLINE=1 TRANSFORMERS_OFFLINE=1 deepspeed /data/codes/lixiang/Video-LLaVA-main/videollava/train/train_mem.py \
    --deepspeed /data/codes/lixiang/Video-LLaVA-main/scripts/zero2.json \
    --model_name_or_path /data/models/models--lmsys--vicuna-7b-v1.5/snapshots/3321f76e3f527bd14065daf69dad9344000a201d \
    --version v1 \
    --data_path /data/codes/lixiang/Video-LLaVA-main/dataset/soccernet_json/soccernet_finetune_matchtime_video_train_official_1115.json /data/codes/lixiang/Video-LLaVA-main/dataset/soccernet_json/nlp_tune.json \
    --image_folder None \
    --image_tower /data/codes/lixiang/Video-LLaVA-main/tower/LanguageBind_Image \
    --video_folder /root/codes/soccernet/caption_anno_clips_matchtime_15soffset/caption_anno_clips_matchtime_15soffset \
    --video_tower /data/codes/lixiang/Video-LLaVA-main/tower/LanguageBind_Video_merge \
    --mm_projector_type mlp2x_gelu \
    --pretrain_mm_mlp_adapter /data/codes/lixiang/Video-LLaVA-main/checkpoints/Video-LLaVA-Pretrain-7B/mm_projector.bin \
    --mm_vision_select_layer -2 \
    --mm_use_im_start_end False \
    --mm_use_im_patch_token False \
    --image_aspect_ratio pad \
    --group_by_modality_length True \
    --bf16 True \
    --output_dir /data/codes/lixiang/Video-LLaVA-main/checkpoints/videollava-7b_my_finetune_matchtime_video_1115 \
    --num_train_epochs 20 \
    --per_device_train_batch_size 8 \
    --per_device_eval_batch_size 8 \
    --gradient_accumulation_steps 1 \
    --evaluation_strategy "no" \
    --save_strategy "steps" \
    --save_steps 100 \
    --save_total_limit 1 \
    --learning_rate 2e-5 \
    --weight_decay 0. \
    --warmup_ratio 0.03 \
    --lr_scheduler_type cosine \
    --logging_steps 1 \
    --tf32 True \
    --model_max_length 2048 \
    --tokenizer_model_max_length 3072 \
    --gradient_checkpointing True \
    --dataloader_num_workers 4 \
    --lazy_preprocess True \
    --report_to tensorboard \
    --cache_dir /data/codes/lixiang/Video-LLaVA-main/cache_dir
