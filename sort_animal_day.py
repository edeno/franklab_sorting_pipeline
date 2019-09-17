#!/usr/bin/env python

# prerequisites:
# pip install spikeforest
# pip install ml_ms4alg

# Spike sorting of one animal-day
import os
from mountaintools import client as mt
import spikeextractors as se
import spikeforest as sf
import ml_ms4alg
import numpy as np
import mlprocessors as mlpr
import spiketoolkit as st
import argparse
import tempfile
import shutil
from shellscript import ShellScript

def main():

    parser = argparse.ArgumentParser(description="Franklab spike sorting for a single animal day")
    parser.add_argument('--input', help='The input directory containing the animal day ephys data', )
    parser.add_argument('--output', help='The output directory where the sorting results will be written')
    parser.add_argument('--num_jobs', help='Number of parallel jobs', required=False, default=1)
    parser.add_argument('--force_run', help='Force the processing to run (no cache)', action='store_true')
    parser.add_argument('--test', help='Only run 2 epochs and 2 ntrodes in each', action='store_true')

    args = parser.parse_args()

    # animal_day_path = '/vortex2/jason/kf19/preprocessing/20170913'
    # animal_day_path = '20170913_kf19'
    # animal_day_output_path = 'test_animal_day_output'

    animal_day_path = args.input
    animal_day_output_path = args.output

    epoch_names = [name for name in sorted(os.listdir(animal_day_path)) if name.endswith('.mda')]
    if args.test:
        epoch_names = epoch_names[0:2]
    epochs = [
        load_epoch(animal_day_path + '/' + name, name=name[0:-4], test=args.test)
        for name in epoch_names
    ]

    mkdir2(animal_day_output_path)

    print('Num parallel jobs: {}'.format(args.num_jobs))

    # Start the job queue
    job_handler = mlpr.ParallelJobHandler(int(args.num_jobs))
    with mlpr.JobQueue(job_handler=job_handler) as JQ:
        for epoch in epochs:
            print('PROCESSING EPOCH: {}'.format(epoch['path']))
            mkdir2(animal_day_output_path + '/' + epoch['name'])
            for ntrode in epoch['ntrodes']:
                print('PROCESSING NTRODE: {}'.format(ntrode['path']))
                mkdir2(animal_day_output_path + '/' + epoch['name'] + '/' + ntrode['name'])
                firings_out = animal_day_output_path + '/' + epoch['name'] + '/' + ntrode['name'] + '/firings.mda'
                recording_file_in = ntrode['recording_file']
                print('Sorting...')
                spike_sorting(
                    recording_file_in,
                    firings_out,
                    args
                )
        JQ.wait()

def load_ntrode(path, *, name):
    return dict(
        name=name,
        path=path,
        recording_file=mt.createSnapshot(path=path)
    )

def load_epoch(path, *, name, test=False):
    ntrode_names = [name for name in sorted(os.listdir(path)) if name.endswith('.mda')]
    if test:
        ntrode_names = ntrode_names[0:2]
    ntrodes = [
        load_ntrode(path + '/' + name2, name=name2[0:-4])
        for name2 in ntrode_names
    ]
    return dict(
        path=path,
        name=name,
        ntrodes=ntrodes
    )


# Start the job queue
def mkdir2(path):
    if not os.path.exists(path):
        os.mkdir(path)

# See: https://github.com/flatironinstitute/spikeforest/blob/master/spikeforest/spikeforestsorters/mountainsort4/mountainsort4.py
class CustomSorting(mlpr.Processor):
    NAME = 'CustomSorting'
    VERSION = '0.1.4'

    recording_file_in = mlpr.Input('Path to raw.mda')
    firings_out = mlpr.Output('Output firings file')

    samplerate = mlpr.FloatParameter("Sampling frequency")

    mask_out_artifacts = mlpr.BoolParameter(optional=True, default=False,
                                description='Whether to mask out artifacts')
    freq_min = mlpr.FloatParameter(
        optional=True, default=300, description='Use 0 for no bandpass filtering')
    freq_max = mlpr.FloatParameter(
        optional=True, default=6000, description='Use 0 for no bandpass filtering')
    whiten = mlpr.BoolParameter(optional=True, default=True,
                                description='Whether to do channel whitening as part of preprocessing')

    detect_sign = mlpr.IntegerParameter(
        'Use -1, 0, or 1, depending on the sign of the spikes in the recording')
    adjacency_radius = mlpr.FloatParameter(
        'Use -1 to include all channels in every neighborhood')
    
    
    clip_size = mlpr.IntegerParameter(
        optional=True, default=50, description='')
    detect_threshold = mlpr.FloatParameter(
        optional=True, default=3, description='')
    detect_interval = mlpr.IntegerParameter(
        optional=True, default=10, description='Minimum number of timepoints between events detected on the same channel')
    noise_overlap_threshold = mlpr.FloatParameter(
        optional=True, default=0.15, description='Use None for no automated curation')

    def run(self):
        # Replace this function with system calls, etc to do
        # mask_out_artifactrs, ml_ms4alg, curation, etc.

        with TemporaryDirectory() as tmpdir:
            if self.mask_out_artifacts:
                print('Masking out artifacts...')
                rec_fname = tmpdir + '/raw.mda'
                _mask_out_artifacts(self.recording_file_in, rec_fname)
            else:
                rec_fname = self.recording_file_in

            X = sf.mdaio.readmda(rec_fname)
            geom = np.zeros((X.shape[0], 2))
            recording = se.NumpyRecordingExtractor(X, samplerate=30000, geom=geom)
            recording = st.preprocessing.bandpass_filter(
                recording=recording,
                freq_min=self.freq_min, freq_max=self.freq_max
            )
            if self.whiten:
                recording = st.preprocessing.whiten(recording=recording)

            num_workers = 2

            sorting = ml_ms4alg.mountainsort4(
                recording=recording,
                detect_sign=self.detect_sign,
                adjacency_radius=self.adjacency_radius,
                clip_size=self.clip_size,
                detect_threshold=self.detect_threshold,
                detect_interval=self.detect_interval,
                num_workers=num_workers
            )
            sf.SFMdaSortingExtractor.write_sorting(
                sorting=sorting,
                save_path=self.firings_out
            )

class TemporaryDirectory():
    def __init__(self):
        pass

    def __enter__(self):
        self._path = tempfile.mkdtemp()
        return self._path

    def __exit__(self, exc_type, exc_val, exc_tb):
        shutil.rmtree(self._path)

    def path(self):
        return self._path

def _mask_out_artifacts(timeseries_in, timeseries_out):
    script = ShellScript('''
    #!/bin/bash
    mp-run-process ms3.mask_out_artifacts --timeseries {} --timeserious_out {}
    '''.format(timeseries_in, timeseries_out))
    script.start()
    retcode = script.wait()
    if retcode != 0:
        raise Exception('problem running ms3.mask_out_artifacts')



def spike_sorting(recording_file_in, firings_out, args):
    CustomSorting.execute(
        mask_out_artifacts=True,
        freq_min=300,
        freq_max=6000,
        whiten=True,
        samplerate=30000,
        recording_file_in=recording_file_in,
        firings_out=firings_out,
        detect_sign=-1,
        adjacency_radius=50,
        _force_run=args.force_run
    )

def mkdir2(path):
    if not os.path.exists(path):
        os.mkdir(path)


        
if __name__ == '__main__':
    main()