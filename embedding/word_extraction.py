import numpy as np
import os
import glob
import pandas as pd
import matplotlib.pyplot as plt
import subprocess
import seaborn as sns
from typing import Set, List, Dict, Set
import functools
from collections import Counter
import csv
import pathlib
import textgrid
import sox
import pickle
from scipy.io import wavfile


def wordcounts(csvpath):
    """count the frequencies of all words in a csv produced by
        https://github.com/mozilla/DeepSpeech/blob/master/bin/import_cv2.py
    """
    all_frequencies = Counter()
    with open(csvpath, "r") as fh:
        reader = csv.reader(fh)
        for ix, row in enumerate(reader):
            if ix == 0:
                continue  # skips header
            words = row[2].split()
            for w in words:
                all_frequencies[w] += 1
    return all_frequencies


def generate_filemap(
    lang_isocode="en",
    alignment_basedir="/home/mark/tinyspeech_harvard/common-voice-forced-alignments/",
):
    """generate a filepath map from mp3 filename to textgrid"""
    filemap = {}
    for root, dirs, files in os.walk(
        pathlib.Path(alignment_basedir) / lang_isocode / "alignments"
    ):
        if not files:
            continue  # this is the top level dir
        for textgrid in files:
            mp3name = os.path.splitext(textgrid)[0]
            if mp3name in filemap:
                raise ValueError(f"{mp3name} already present in filemap")
            filemap[mp3name] = os.path.join(root, textgrid)
    return filemap


def generate_wordtimings(
    words_to_search_for: Set[str],
    mp3_to_textgrid,
    lang_isocode="en",
    alignment_basedir="/home/mark/tinyspeech_harvard/common-voice-forced-alignments/",
):
    """for a set of desired words, use alignment TextGrids to return start and end times"""
    # word: pseudo-datframe of [(mp3_filename, start_time_s, end_time_s)]
    timings = {w: [] for w in words_to_search_for}
    notfound = []
    # common voice csv from DeepSpeech/import_cv2.py
    csvpath = pathlib.Path(alignment_basedir) / lang_isocode / "validated.csv"
    with open(csvpath, "r") as fh:
        reader = csv.reader(fh)
        for ix, row in enumerate(reader):
            if ix == 0:
                continue  # skips header
            if ix % 80_000 == 0:
                print(ix)
            # find words in common_words set from each row of csv
            mp3name_no_extension = os.path.splitext(row[0])[0]
            words = row[2].split()
            for word in words:
                if word not in words_to_search_for:
                    continue
                # get alignment timings for this word
                try:
                    tgf = mp3_to_textgrid[mp3name_no_extension]
                except KeyError as e:
                    notfound.append((mp3name_no_extension, word))
                    continue
                tg = textgrid.TextGrid.fromFile(tgf)
                for interval in tg[0]:
                    if interval.mark != word:
                        continue
                    start_s = interval.minTime
                    end_s = interval.maxTime
                    timings[word].append((mp3name_no_extension, start_s, end_s))
    return timings, notfound


def full_transcription_timings(textgrid_path):
    """[(word, start, end)] for a full textgrid
        note: word often will be blank, denoting pauses
    """
    tg = textgrid.TextGrid.fromFile(textgrid_path)
    word_timings = []
    for interval in tg[0]:
        word_timings.append((interval.mark, interval.minTime, interval.maxTime))
    return word_timings

def extract_one_second(duration_s: float, start_s: float, end_s: float):
    """
    return one second around the midpoint between start_s and end_s
    """
    if duration_s < 1:
        return (0, duration_s)
    center_s = start_s + ((end_s - start_s) / 2.0)
    new_start_s = center_s - 0.5
    new_end_s = center_s + 0.5
    if new_end_s > duration_s:
        new_end_s = duration_s
        new_start_s = duration_s - 1.0
    if new_start_s < 0:
        new_start_s = 0
        new_end_s = np.minimum(duration_s, new_start_s + 1.0)
    return (new_start_s, new_end_s)


def extract_shot_from_mp3(
    mp3name_no_ext,
    start_s,
    end_s,
    dest_dir,
    cv_clipsdir=pathlib.Path(
        "/home/mark/tinyspeech_harvard/common_voice/cv-corpus-6.1-2020-12-11/en/clips"
    ),
):
    mp3path = cv_clipsdir / (mp3name_no_ext + ".mp3")
    if not os.path.exists(mp3path):
        raise ValueError("could not find", mp3path)

    duration = sox.file_info.duration(mp3path)
    if end_s - start_s < 1:
        pad_amt_s = (1.0 - (end_s - start_s)) / 2.0
    else:  # utterance is already longer than 1s, trim instead
        start_s, end_s = extract_one_second(duration, start_s, end_s)
        pad_amt_s = 0

    if not os.path.isdir(dest_dir):
        raise ValueError(dest_dir, "does not exist")
    dest = dest_dir / (mp3name_no_ext + ".wav")
    # words can appear multiple times in a sentence: above should have filtered these
    if os.path.exists(dest):
        raise ValueError("already exists:", dest)

    transformer = sox.Transformer()
    transformer.convert(samplerate=16000)  # from 48K mp3s
    transformer.trim(start_s, end_s)
    # use smaller fadein/fadeout since we are capturing just the word
    # TODO(mmaz) is this appropriately sized?
    transformer.fade(fade_in_len=0.025, fade_out_len=0.025)
    transformer.pad(start_duration=pad_amt_s, end_duration=pad_amt_s)
    transformer.build(str(mp3path), str(dest))
