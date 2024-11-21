import math
import os
os.environ['RANK'] = str(0)
os.environ['CUDA_VISIBLE_DEVICES']='1'

import argparse
import json
import random
from glob  import glob

import sys
sys.path.append('/data/codes/lixiang/Video-LLaVA-main')

import torch
import transformers
import numpy as np
from tqdm import tqdm
from videollava.conversation import conv_templates, SeparatorStyle
from videollava.constants import DEFAULT_IM_START_TOKEN, DEFAULT_IMAGE_TOKEN, DEFAULT_IM_END_TOKEN, IMAGE_TOKEN_INDEX, DEFAULT_VID_START_TOKEN, DEFAULT_VID_END_TOKEN
from videollava.mm_utils import get_model_name_from_path, tokenizer_image_token, KeywordsStoppingCriteria
from videollava.model.builder import load_pretrained_model
from videollava.model.language_model.llava_llama import LlavaLlamaForCausalLM
from videollava.train.train import smart_tokenizer_and_embedding_resize

from nltk.translate.bleu_score import sentence_bleu, SmoothingFunction

from torch.utils.data import Dataset
from torch.utils.data import DataLoader
from rouge_score import rouge_scorer
from pycocoevalcap.meteor.meteor import Meteor
from pycocoevalcap.cider.cider import Cider

"""
CUDA_VISIBLE_DEVICES=0 PYTHONUNBUFFERED=1 deepspeed /data/codes/lixiang/Video-LLaVA-main/videollava/eval/my_inference.py --model_path /data/codes/lixiang/Video-LLaVA-main/checkpoints/videollava-7b_my_finetune_matchtime_0909_2_epoch10/checkpoint-2600 --cache_dir /data/codes/lixiang/Video-LLaVA-main/cache_dir/ --video_dir /data/codes/lixiang/soccernet-matchtime/ --gt_file /data/codes/lixiang/Video-LLaVA-main/dataset/soccernet_json/soccernet_finetune_matchtime_eval.json --output_dir /data/codes/lixiang/Video-LLaVA-main/dataset/ 
"""

class MultimodalDataset(Dataset):
    """Dataset for supervised fine-tuning with multimodal data."""

    def __init__(self, data_path,
                #  tokenizer: transformers.PreTrainedTokenizer,
                 data_args):
        super(MultimodalDataset, self).__init__()
        self.data_args = data_args
        self.gt_samples = json.load(open(args.gt_file, 'r'))

    def __len__(self):
        return len(self.gt_samples)


    def __getitem__(self, i):
        video_name = self.gt_samples[i]['video']
        audio_name = self.gt_samples[i]['audio']
        question = self.gt_samples[i]['conversations'][0]['value']
        id = self.gt_samples[i]['id']
        answer = self.gt_samples[i]['conversations'][1]['value']

        sample_set = {
            'video': os.path.join(self.data_args.video_dir, video_name),
            'audio': os.path.join(args.video_dir, audio_name),
            'id': id,
            'question': question,
            'answer': answer
        }

        return sample_set
    
    def collate_fn(self, instances):
        # print(instances.keys())
        batch_set = {
                    'video': [ins['video'] for ins in instances], 
                    'audio': [ins['audio'] for ins in instances], 
                    #   'metadata': all_metadata[metadata_name] if metadata_name is not None else None,
                    'id': [ins['id'] for ins in instances], 
                    'question': [ins['question'] for ins in instances], 
                    'answer': [ins['answer'] for ins in instances], 
                    }
        return batch_set

def split_list(lst, n):
    """Split a list into n (roughly) equal-sized chunks"""
    chunk_size = math.ceil(len(lst) / n)  # integer division
    return [lst[i:i+chunk_size] for i in range(0, len(lst), chunk_size)]


def get_chunk(lst, n, k):
    chunks = split_list(lst, n)
    return chunks[k]

def parse_args():
    """
    Parse command-line arguments.
    """
    parser = argparse.ArgumentParser()

    # Define the command-line arguments
    parser.add_argument('--model_path', help='', required=True)
    parser.add_argument('--cache_dir', help='', required=True)
    parser.add_argument('--video_dir', help='Directory containing video files.', required=True)
    parser.add_argument('--gt_file', help='Path to the ground truth file', required=True)
    # parser.add_argument('--gt_file_question', help='Path to the ground truth file containing question.', required=True)
    # parser.add_argument('--gt_file_answers', help='Path to the ground truth file containing answers.', required=True)
    parser.add_argument('--output_dir', help='Directory to save the model results JSON.', required=True)
    parser.add_argument('--output_name', default='video_qa_pred_res', help='Name of the file for storing results JSON.')
    parser.add_argument("--num_chunks", type=int, default=1)
    parser.add_argument("--chunk_idx", type=int, default=0)
    parser.add_argument("--device", type=str, required=False, default='cuda:0')
    parser.add_argument('--model_base', help='', default=None, type=str, required=False)
    parser.add_argument("--model_max_length", type=int, required=False, default=2048)
    parser.add_argument("--local_rank", type=int, default=0)

    return parser.parse_args()

def get_model_output(model, processor, tokenizer, sample, qs, args):
    if model.config.mm_use_im_start_end:
        qs = DEFAULT_VID_START_TOKEN + ''.join([DEFAULT_IMAGE_TOKEN]*8) + DEFAULT_VID_END_TOKEN + '\n' + qs
    else:
        qs = ''.join([DEFAULT_IMAGE_TOKEN]*8) + '\n' + qs

    conv_mode = "llava_v1"
    args.conv_mode = conv_mode

    conv = conv_templates[args.conv_mode].copy()
    conv.append_message(conv.roles[0], qs)
    conv.append_message(conv.roles[1], None)
    prompt = conv.get_prompt()

    output = {'video': None,
              'audio': None,
              'metadata': None}

    video_processor = processor['video']
    audio_processor = processor['audio'] if 'audio' in processor.keys() else None

    video_tensor = video_processor.preprocess(sample['video'], return_tensors='pt')['pixel_values'][0].half().to(args.device).unsqueeze(0)
    video_tensor = model.encode_videos(video_tensor)
    output['video'] = video_tensor.to('cpu')

    if audio_processor is not None and sample['audio'] is not None and os.path.exists(sample['audio']):
        audio_tensor = audio_processor.preprocess(sample['audio'], return_tensors='pt')['pixel_values'][0].half().to(args.device).unsqueeze(0)
        audio_tensor = model.encode_audios(audio_tensor)
        output['audio'] = audio_tensor.to('cpu')
    
    # if sample['metadata'] is not None:
    #     meta_text = sample['metadata']
    #     input_ids = tokenizer(
    #         meta_text,
    #         return_tensors="pt",
    #         padding="longest",
    #         max_length=tokenizer.model_max_length,
    #         truncation=True,
    #     ).input_ids
        


    # print(video_tensor.shape)
    # input_ids = tokenizer_image_token(prompt, tokenizer, IMAGE_TOKEN_INDEX, return_tensors='pt').unsqueeze(0).to(args.device)

    # stop_str = conv.sep if conv.sep_style != SeparatorStyle.TWO else conv.sep2
    # keywords = [stop_str]
    # stopping_criteria = KeywordsStoppingCriteria(keywords, tokenizer, input_ids)

    # with torch.inference_mode():
    #     output_ids = model.generate(input_ids, images=[video_tensor], do_sample=True, temperature=0.7, max_new_tokens=1024, use_cache=True, stopping_criteria=[stopping_criteria])

    # input_token_len = input_ids.shape[1]
    # n_diff_input_output = (input_ids != output_ids[:, :input_token_len]).sum().item()
    # if n_diff_input_output > 0:
    #     print(f'[Warning] {n_diff_input_output} output_ids are not the same as the input_ids')
    # outputs = tokenizer.batch_decode(output_ids[:, input_token_len:], skip_special_tokens=True)[0]
    # outputs = outputs.strip()
    # if outputs.endswith(stop_str):
    #     outputs = outputs[:-len(stop_str)]
    # outputs = outputs.strip()
    # print(outputs)

    return output

test_urllocal_list = ['england_epl/2014-2015/2015-05-17 - 18-00 Manchester United 1 - 1 Arsenal', 'england_epl/2015-2016/2015-08-16 - 18-00 Manchester City 3 - 0 Chelsea', 'england_epl/2015-2016/2015-08-23 - 15-30 West Brom 2 - 3 Chelsea', 'england_epl/2015-2016/2015-08-29 - 17-00 Liverpool 0 - 3 West Ham', 'england_epl/2015-2016/2015-09-20 - 18-00 Southampton 2 - 3 Manchester United', 'england_epl/2015-2016/2015-09-26 - 19-30 Newcastle Utd 2 - 2 Chelsea', 'england_epl/2015-2016/2015-10-03 - 19-30 Chelsea 1 - 3 Southampton', 'england_epl/2015-2016/2015-10-24 - 17-00 West Ham 2 - 1 Chelsea', 'england_epl/2015-2016/2015-11-07 - 20-30 Stoke City 1 - 0 Chelsea', 'england_epl/2015-2016/2015-11-08 - 19-00 Arsenal 1 - 1 Tottenham', 'england_epl/2015-2016/2015-12-28 - 20-30 Manchester United 0 - 0 Chelsea', 'england_epl/2015-2016/2016-02-03 - 22-45 Watford 0 - 0 Chelsea', 'england_epl/2015-2016/2016-03-01 - 22-45 Norwich 1 - 2 Chelsea', 'england_epl/2016-2017/2016-08-27 - 14-30 Tottenham 1 - 1 Liverpool', 'england_epl/2016-2017/2016-09-24 - 14-30 Manchester United 4 - 1 Leicester', 'england_epl/2016-2017/2016-10-15 - 14-30 Chelsea 3 - 0 Leicester', 'england_epl/2016-2017/2017-01-21 - 15-30 Liverpool 2 - 3 Swansea', 'england_epl/2016-2017/2017-05-06 - 17-00 Leicester 3 - 0 Watford', 'europe_uefa-champions-league/2014-2015/2014-11-04 - 20-00 Zenit Petersburg 1 - 2 Bayer Leverkusen', 'europe_uefa-champions-league/2014-2015/2015-02-24 - 22-45 Manchester City 1 - 2 Barcelona', 'europe_uefa-champions-league/2014-2015/2015-03-10 - 22-45 Real Madrid 3 - 4 Schalke', 'europe_uefa-champions-league/2014-2015/2015-03-17 - 22-45 Monaco 0 - 2 Arsenal', 'europe_uefa-champions-league/2014-2015/2015-04-15 - 21-45 FC Porto 3 - 1 Bayern Munich', 'europe_uefa-champions-league/2014-2015/2015-04-22 - 21-45 Real Madrid 1 - 0 Atl. Madrid', 'europe_uefa-champions-league/2014-2015/2015-05-05 - 21-45 Juventus 2 - 1 Real Madrid', 'europe_uefa-champions-league/2015-2016/2015-09-29 - 21-45 Bayern Munich 5 - 0 D. Zagreb', 'europe_uefa-champions-league/2015-2016/2015-11-03 - 22-45 Real Madrid 1 - 0 Paris SG', 'europe_uefa-champions-league/2015-2016/2015-11-03 - 22-45 Sevilla 1 - 3 Manchester City', 'europe_uefa-champions-league/2015-2016/2015-11-03 - 22-45 Shakhtar Donetsk 4 - 0 Malmo FF', 'europe_uefa-champions-league/2015-2016/2015-11-25 - 22-45 Shakhtar Donetsk 3 - 4 Real Madrid', 'europe_uefa-champions-league/2015-2016/2016-04-05 - 21-45 Bayern Munich 1 - 0 Benfica', 'europe_uefa-champions-league/2016-2017/2016-11-01 - 20-45 Besiktas 1 - 1 Napoli', 'europe_uefa-champions-league/2016-2017/2016-11-01 - 22-45 Manchester City 3 - 1 Barcelona', 'europe_uefa-champions-league/2016-2017/2016-11-23 - 22-45 Arsenal 2 - 2 Paris SG', 'europe_uefa-champions-league/2016-2017/2017-03-08 - 22-45 Barcelona 6 - 1 Paris SG', 'europe_uefa-champions-league/2016-2017/2017-04-12 - 21-45 Bayern Munich 1 - 2 Real Madrid', 'europe_uefa-champions-league/2016-2017/2017-05-02 - 21-45 Real Madrid 3 - 0 Atl. Madrid', 'france_ligue-1/2016-2017/2016-08-28 - 21-45 Monaco 3 - 1 Paris SG', 'france_ligue-1/2016-2017/2016-11-30 - 23-00 Paris SG 2 - 0 Angers', 'germany_bundesliga/2014-2015/2015-05-09 - 16-30 Bayern Munich 0 - 1 FC Augsburg', 'germany_bundesliga/2015-2016/2015-08-29 - 19-30 Bayern Munich 3 - 0 Bayer Leverkusen', 'germany_bundesliga/2015-2016/2015-09-12 - 16-30 Bayern Munich 2 - 1 FC Augsburg', 'germany_bundesliga/2015-2016/2015-10-24 - 16-30 Bayern Munich 4 - 0 FC Koln', 'germany_bundesliga/2015-2016/2015-11-08 - 17-30 Dortmund 3 - 2 Schalke', 'germany_bundesliga/2016-2017/2016-09-10 - 19-30 RB Leipzig 1 - 0 Dortmund', 'germany_bundesliga/2016-2017/2016-10-01 - 19-30 Bayer Leverkusen 2 - 0 Dortmund', 'germany_bundesliga/2016-2017/2016-11-05 - 17-30 Hamburger SV 2 - 5 Dortmund', 'germany_bundesliga/2016-2017/2016-11-19 - 20-30 Dortmund 1 - 0 Bayern Munich', 'germany_bundesliga/2016-2017/2016-12-16 - 22-30 Hoffenheim 2 - 2 Dortmund', 'germany_bundesliga/2016-2017/2017-01-21 - 17-30 SV Werder Bremen 1 - 2 Dortmund', 'germany_bundesliga/2016-2017/2017-01-29 - 19-30 1. FSV Mainz 05 1 - 1 Dortmund', 'germany_bundesliga/2016-2017/2017-03-04 - 17-30 Dortmund 6 - 2 Bayer Leverkusen', 'germany_bundesliga/2016-2017/2017-04-29 - 16-30 Dortmund 0 - 0 FC Koln', 'italy_serie-a/2014-2015/2015-04-29 - 21-45 Juventus 3 - 2 Fiorentina', 'italy_serie-a/2015-2016/2015-08-29 - 21-45 AC Milan 2 - 1 Empoli', 'italy_serie-a/2015-2016/2015-09-20 - 16-00 Genoa 0 - 2 Juventus', 'italy_serie-a/2015-2016/2015-09-27 - 21-45 Inter 1 - 4 Fiorentina', 'italy_serie-a/2016-2017/2016-08-27 - 21-45 Napoli 4 - 2 AC Milan', 'italy_serie-a/2016-2017/2016-09-11 - 16-00 AC Milan 0 - 1 Udinese', 'italy_serie-a/2016-2017/2016-09-20 - 21-45 AC Milan 2 - 0 Lazio', 'italy_serie-a/2016-2017/2016-09-24 - 21-45 Napoli 2 - 0 Chievo', 'italy_serie-a/2016-2017/2016-09-25 - 13-30 Torino 3 - 1 AS Roma', 'italy_serie-a/2016-2017/2016-09-25 - 21-45 Fiorentina 0 - 0 AC Milan', 'italy_serie-a/2016-2017/2016-10-02 - 21-45 AS Roma 2 - 1 Inter', 'italy_serie-a/2016-2017/2016-11-20 - 17-00 Atalanta 2 - 1 AS Roma', 'italy_serie-a/2016-2017/2016-11-26 - 22-45 Empoli 1 - 4 AC Milan', 'italy_serie-a/2016-2017/2016-12-04 - 17-00 Lazio 0 - 2 AS Roma', 'italy_serie-a/2016-2017/2017-01-08 - 17-00 Genoa 0 - 1 AS Roma', 'italy_serie-a/2016-2017/2017-01-29 - 17-00 Sampdoria 3 - 2 AS Roma', 'italy_serie-a/2016-2017/2017-02-07 - 22-45 AS Roma 4 - 0 Fiorentina', 'italy_serie-a/2016-2017/2017-02-25 - 20-00 Napoli 0 - 2 Atalanta', 'italy_serie-a/2016-2017/2017-02-26 - 22-45 Inter 1 - 3 AS Roma', 'italy_serie-a/2016-2017/2017-04-01 - 21-45 AS Roma 2 - 0 Empoli', 'italy_serie-a/2016-2017/2017-04-30 - 21-45 Inter 0 - 1 Napoli', 'italy_serie-a/2016-2017/2017-05-20 - 21-45 Napoli 4 - 1 Fiorentina', 'spain_laliga/2014-2015/2015-02-14 - 20-00 Real Madrid 2 - 0 Dep. La Coruna', 'spain_laliga/2014-2015/2015-04-18 - 21-00 Real Madrid 3 - 1 Malaga', 'spain_laliga/2014-2015/2015-04-25 - 17-00 Espanyol 0 - 2 Barcelona', 'spain_laliga/2014-2015/2015-04-29 - 21-00 Real Madrid 3 - 0 Almeria', 'spain_laliga/2014-2015/2015-05-02 - 17-00 Cordoba 0 - 8 Barcelona', 'spain_laliga/2014-2015/2015-05-09 - 19-00 Barcelona 2 - 0 Real Sociedad', 'spain_laliga/2015-2016/2015-08-29 - 23-30 Real Madrid 5 - 0 Betis', 'spain_laliga/2015-2016/2015-09-19 - 17-00 Real Madrid 1 - 0 Granada CF', 'spain_laliga/2015-2016/2015-11-08 - 18-00 Barcelona 3 - 0 Villarreal', 'spain_laliga/2015-2016/2015-12-05 - 18-00 Real Madrid 4 - 1 Getafe', 'spain_laliga/2015-2016/2015-12-30 - 18-00 Real Madrid 3 - 1 Real Sociedad', 'spain_laliga/2015-2016/2016-02-27 - 18-00 Real Madrid 0 - 1 Atl. Madrid', 'spain_laliga/2015-2016/2016-03-02 - 23-00 Levante 1 - 3 Real Madrid', 'spain_laliga/2015-2016/2016-05-08 - 18-00 Real Madrid 3 - 2 Valencia', 'spain_laliga/2015-2016/2016-05-14 - 18-00 Dep. La Coruna 0 - 2 Real Madrid', 'spain_laliga/2016-2017/2016-09-10 - 21-30 Barcelona 1 - 2 Alaves', 'spain_laliga/2016-2017/2016-09-21 - 21-00 Real Madrid 1 - 1 Villarreal', 'spain_laliga/2016-2017/2016-09-24 - 21-45 Las Palmas 2 - 2 Real Madrid', 'spain_laliga/2016-2017/2016-11-26 - 18-15 Real Madrid 2 - 1 Gijon', 'spain_laliga/2016-2017/2016-12-18 - 22-45 Barcelona 4 - 1 Espanyol', 'spain_laliga/2016-2017/2017-02-11 - 22-45 Osasuna 1 - 3 Real Madrid', 'spain_laliga/2016-2017/2017-03-12 - 22-45 Real Madrid 2 - 1 Betis', 'spain_laliga/2016-2017/2017-04-02 - 17-15 Real Madrid 3 - 0 Alaves', 'spain_laliga/2016-2017/2017-04-08 - 21-45 Malaga 2 - 0 Barcelona', 'spain_laliga/2016-2017/2017-04-26 - 20-30 Barcelona 7 - 1 Osasuna']



def run_inference(args):
    """
    Run inference on ActivityNet QA DataSet using the Video-ChatGPT model.

    Args:
        args: Command-line arguments.
    """
    # Initialize the model
    model_name = get_model_name_from_path(args.model_path)
    tokenizer, model, processor, context_len = load_pretrained_model(args.model_path, args.model_base, model_name)
    model = model.to(args.device)
    model.eval()

    # answers_file = os.path.join(args.output_dir, f"{args.output_name}.json")
    # os.makedirs(args.output_dir, exist_ok=True)
    # ans_file = open(answers_file, "w")
    # all_metadata = np.load('/data/codes/lixiang/Video-LLaVA-main/dataset/soccernet_json/transcriptions_matchtime_audio.npy',allow_pickle=True).tolist()
    result_file = os.path.join(args.output_dir, "multimodal_tensor_1119.npy")

    # Create the output directory if it doesn't exist
    if not os.path.exists(args.output_dir):
        os.makedirs(args.output_dir)

    output_list = []  # List to store the output results


    video_formats = ['.mp4', '.avi', '.mov', '.mkv']

    # Iterate over each sample in the ground truth file
    # gt_samples = json.load(open(args.gt_file, 'r'))
    dataset = MultimodalDataset(args.gt_file, args)
    dataloader = DataLoader(dataset, batch_size=32, collate_fn=dataset.collate_fn)
    index = 0
    batch_size = 32
    # for b_index in tqdm(range(0, len(gt_samples), batch_size)):
        # Load the video file
        # for fmt in tqdm(video_formats):  # Added this line
        # cur_bs_samples = gt_samples[b_index * batch_size : (b_index+1) * batch_size]
    for batch in tqdm(dataloader):
        torch.cuda.empty_cache()

        index+=1
        try:
            with torch.inference_mode():
                batch['pred'] = get_model_output(model, processor, tokenizer, batch, args)
        except Exception as E:
            print(E)
            continue
        output_list.append(batch)

        if index % 50 == 0:
            np.save(result_file, output_list)


        #     # break
        # if index >:
        #     break

    # ans_file.close()
    
    # Save the output list to a JSON file
    # with open(os.path.join(args.output_dir, f"{args.output_name}.json"), 'w') as file:
    #     json.dump(output_list, file)


if __name__ == "__main__":
    args = parse_args()
    run_inference(args)