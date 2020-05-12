from typing import Dict, List 

import torch
import torch.nn as nn
import torch.optim as optim 

import numpy as np 

from .module import Module

'''
Based on:
https://github.com/google-research/disentanglement_lib/blob/master/disentanglement_lib/evaluation/metrics/factor_vae.py
'''

def build_FactorVAEDisentanglementMetricModule(id:str,
                               config:Dict[str,object],
                               input_stream_ids:Dict[str,str]=None) -> Module:
    return FactorVAEDisentanglementMetricModule(id=id,
                                config=config, 
                                input_stream_ids=input_stream_ids)


class FactorVAEDisentanglementMetricModule(Module):
    def __init__(self,
                 id:str,
                 config:Dict[str,object],
                 input_stream_ids:Dict[str,str]=None):

        input_stream_ids = {
            "modules:logger:ref":"logger",
            "logs_dict":"logs_dict",
            "signals:epoch":"epoch",
            "signals:mode":"mode",

            "signals:end_of_dataset":"end_of_dataset",  
            # boolean: whether the current batch/datasample is the last of the current dataset/mode.
            "signals:end_of_repetition_sequence":"end_of_repetition_sequence",
            # boolean: whether the current sample(observation from the agent of the current batch/datasample) 
            # is the last of the current sequence of repetition.
            "signals:end_of_communication":"end_of_communication",
            # boolean: whether the current communication round is the last of 
            # the current dialog.
            
            "modules:current_speaker:ref:ref_agent:cnn_encoder":"model",
            "current_dataset:ref":"dataset",

            "modules:current_speaker:ref:ref_agent:features":"representations",
            'current_dataloader:sample:speaker_experiences':'experiences', 
            'current_dataloader:sample:speaker_exp_latents':'latent_representations', 
            'current_dataloader:sample:speaker_exp_latents_values':'latent_values_representations',
            'current_dataloader:sample:speaker_indices':'indices', 
            
        }

        super(FactorVAEDisentanglementMetricModule, self).__init__(id=id,
                                                 type="FactorVAEDisentanglementMetricModule",
                                                 config=config,
                                                 input_stream_ids=input_stream_ids)
        
        # Default = 0.0
        self.repr_dim_filtering_threshold = self.config['threshold']
        self.random_state = np.random.RandomState(self.config['random_state_seed'])
        
        self.representations = []
        self.latent_representations = []
        self.latent_values_representations = []
        self.representations_indices = []
        self.indices = []


        self.end_of_ = [value for key,value in input_stream_ids.items() if 'end_of_' in value]
    
    def _prune_dims(self, variances):
        """Mask for dimensions collapsed to the prior."""
        scale_z = np.sqrt(variances)
        return scale_z >= self.repr_dim_filtering_threshold

    def _generate_training_batch(self,
                                 dataset,
                                 model,
                                 batch_size,
                                 nbr_points,
                                 global_variances, 
                                 active_dims):
        """
        Sample a set of training samples based on a batch of ground-truth data.
        
        Args:
            dataset: dataset to be sampled from.
            model: model that takes observations as input and
                    outputs a dim_representation sized representation for each observation.
            batch_size: Number of points to be used to compute the training_sample.
            nbr_points: Number of points to be sampled for training/evaluation set.
            global_variances: Numpy vector with variances for all dimensions of
                              representation.
            active_dims: Indexes of active dimensions.
        Returns:
            (num_factors, dim_representation)-sized numpy array with votes.
        
        """
        self.nbr_factors = self.latent_representations.shape[-1]
        votes = np.zeros((self.nbr_factors, global_variances.shape[0]),
                       dtype=np.int64)
        
        for _ in range(nbr_points):
            factor_index, argmin = self._generate_training_sample(
                dataset,
                model,
                batch_size, 
                global_variances,
                active_dims)
            votes[factor_index, argmin] += 1
        return votes

    def _generate_training_sample(self, 
                                  dataset, 
                                  model,
                                  batch_size, 
                                  global_variances,
                                  active_dims):
        """
        Sample a single training sample based on a mini-batch of ground-truth data.
        
        Args:
        dataset: dataset to be sampled from.
        model: model that takes observation as input and
                outputs a representation.
        batch_size: Number of points to be used to compute the training_sample.
        global_variances: Numpy vector with variances for all dimensions of
                            representation.
        active_dims: Indexes of active dimensions.
        
        Returns:
            factor_index: Index of factor coordinate to be used.
            argmin: Index of representation coordinate with the least variance.
        
        """
        
        # Select random coordinate to keep fixed.
        if self.config['active_factors_only']:
            factor_index = np.random.choice(self.active_latent_dims)
        else:
            factor_index = self.random_state.randint(self.nbr_factors)
        
        if self.config['resample']:
            raise NotImplementedError
            # Sample two mini batches of latent variables.
            factors = dataset.sample_factors(batch_size, self.random_state)
            # Fix the selected factor across mini-batch.
            factors[:, factor_index] = factors[0, factor_index]
            # Obtain the observations.
            observations = dataset.sample_observations_from_factors(
              factors, 
              self.random_state
            )

            representations = model(observations)
            local_variances = np.var(representations, axis=0, ddof=1)
            argmin = np.argmin(local_variances[active_dims] /
                             global_variances[active_dims])
        else:
            # Sample from the current epoch's samples the factor value to fix:
            sample_to_fix_factor_value_idx = np.random.choice(np.arange(self.latent_representations.shape[0]))
            factor_value = self.latent_representations[sample_to_fix_factor_value_idx,...,factor_index]
            
            # Sample from the current epoch the indices of relevant samples:
            relevant_samples_indices = [
                it for it, lr in enumerate(self.latent_representations) 
                if lr[...,factor_index]== factor_value
            ]
            if len(relevant_samples_indices) < batch_size:
                if self.config['verbose']:
                    print(f"WARNING: generate_training_sample ::\
                     too few relevant samples: {len(relevant_samples_indices)} < batch_size={batch_size}.\n\
                     Falling back on this value...")
                batch_size = len(relevant_samples_indices)
            # No issue of batch_size = 0 ....
            relevant_samples_indices_sampled = np.random.choice(relevant_samples_indices, 
                size=batch_size,
                replace=False)
            relevant_representations = self.representations[relevant_samples_indices_sampled]
            local_variances = np.var(relevant_representations, axis=0, ddof=1)
            argmin = np.argmin(local_variances[active_dims]/global_variances[active_dims])

        return factor_index, argmin

    def compute(self, input_streams_dict:Dict[str,object]) -> Dict[str,object] :
        '''
        '''
        outputs_stream_dict = {}


        logs_dict = input_streams_dict['logs_dict']
        mode = input_streams_dict['mode']
        epoch = input_streams_dict['epoch']
        
        if epoch % self.config['epoch_period'] == 1:
            representations = input_streams_dict['representations']
            self.representations.append(representations.cpu().detach().numpy())
            latent_representations = input_streams_dict['latent_representations']
            self.latent_representations.append(latent_representations.cpu().detach().numpy())
            latent_values_representations = input_streams_dict['latent_values_representations']
            self.latent_values_representations.append(latent_values_representations.cpu().detach().numpy())
            indices = input_streams_dict['indices']
            self.indices.append(indices.cpu().detach().numpy())

            # Is it the end of the epoch?
            end_of_epoch = all([
              input_streams_dict[key]
              for key in self.end_of_]
            )
            
            if end_of_epoch:
                self.representations = np.concatenate(self.representations, axis=0).squeeze()
                self.latent_representations = np.concatenate(self.latent_representations, axis=0).squeeze()
                self.latent_values_representations = np.concatenate(self.latent_values_representations, axis=0).squeeze()
                self.indices = np.concatenate(self.indices, axis=0).squeeze()


                model = input_streams_dict['model']
                dataset = input_streams_dict['dataset']
                logger = input_streams_dict['logger']

                global_variances = np.var(self.representations, axis=0, ddof=1)
                latent_global_variances = np.var(self.latent_representations, axis=0, ddof=1)

                active_dims = self._prune_dims(global_variances)
                self.active_latent_dims = [idx for idx, var in enumerate(latent_global_variances)
                                            if var > 0.0]
                scores_dict = {}

                if not active_dims.any():
                    scores_dict["train_accuracy"] = 0.
                    scores_dict["eval_accuracy"] = 0.
                    scores_dict["num_active_dims"] = 0
                else:
                    training_votes = self._generate_training_batch(
                        dataset=dataset,
                        model=model, 
                        batch_size=self.config['batch_size'],
                        nbr_points=self.config['nbr_train_points'], 
                        global_variances=global_variances, 
                        active_dims=active_dims)
                    
                    classifier = np.argmax(training_votes, axis=0)
                    other_index = np.arange(training_votes.shape[1])

                    train_accuracy = np.sum(
                      training_votes[classifier, other_index]) * 1. / np.sum(training_votes)
                    
                    eval_votes = self._generate_training_batch(
                        dataset=dataset,
                        model=model, 
                        batch_size=self.config['batch_size'],
                        nbr_points=self.config['nbr_eval_points'],
                        global_variances=global_variances,
                        active_dims=active_dims
                    )

                    eval_votes_per_factor = eval_votes.sum(-1)
                    eval_votes_per_factor += (eval_votes_per_factor==0)*np.ones_like(eval_votes_per_factor)
                    per_factor_eval_accuracy = eval_votes.max(-1)/eval_votes_per_factor 
                    '''
                    eval_votes_per_repr_dim = eval_votes.sum(0)
                    eval_votes_per_repr_dim += (eval_votes_per_repr_dim==0)*np.ones_like(eval_votes_per_repr_dim)
                    per_repr_dim_eval_accuracy = eval_votes[classifier]/eval_votes_per_repr_dim 
                    '''

                    eval_accuracy = np.sum(eval_votes[classifier,
                                                    other_index]) * 1. / np.sum(eval_votes)
                    
                    scores_dict["train_accuracy"] = train_accuracy*100.0
                    scores_dict["eval_accuracy"] = eval_accuracy*100.0
                    for idx, acc in enumerate(per_factor_eval_accuracy):
                        scores_dict[f"eval_accuracy_{idx}"] = acc*100.0
                        
                    scores_dict["num_active_dims"] = len(active_dims)
                    
                logs_dict[f'{mode}/DisentanglementMetric/FactorVAE/train_accuracy'] = scores_dict['train_accuracy']
                logs_dict[f'{mode}/DisentanglementMetric/FactorVAE/eval_accuracy/mean'] = scores_dict['eval_accuracy']
                for idx, acc in enumerate(per_factor_eval_accuracy):
                    logs_dict[f'{mode}/DisentanglementMetric/FactorVAE/eval_accuracy/factor_{idx}'] = scores_dict[f"eval_accuracy_{idx}"]
                logs_dict[f'{mode}/DisentanglementMetric/FactorVAE/nbr_active_dims'] = scores_dict['num_active_dims']
                    
                self.representations = []
                self.latent_representations = []
                self.latent_values_representations = []
                self.representations_indices = []
                self.indices = []
            
        return outputs_stream_dict
    