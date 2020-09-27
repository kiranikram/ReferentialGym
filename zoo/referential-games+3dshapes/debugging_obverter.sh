python -m ipdb -c c train_obverter.py \
--parent_folder ./debug/Sept23/HalfNew/\
New+Normalize-PackPad+innerModel-LearningNotTarget+OneMinuxMaxProb\
/Tau1e0-BMM-CosSim+DecisionHeads2-SigmoidDecisionHeads+Normalize+InnerModelGen+OneMinuxMaxProb+StopPadding-WholeSentence-\
UseStopWord+StabEps1m8-LogSoftmaxAfterLogOnSigmoid+ScaleNL1e4+AlwaysArgmax\
/SymbolEmb64+GRU64+CNN256+Decision128/ \
--use_cuda --seed 0 \
--obverter_nbr_games_per_round 20 --obverter_threshold_to_stop_message_generation 0.95 \
--emb_dropout_prob 0.0 --dropout_prob 0.0 --use_sentences_one_hot_vectors \
--batch_size 50 --mini_batch_size 256 --resizeDim 128 --arch BaselineCNN \
--descriptive --descriptive_ratio 0.25 \
--max_sentence_length 20 --vocab_size 5 --epoch 10000 \
--symbol_processing_nbr_hidden_units 64 --symbol_embedding_size 64 \
--object_centric --nbr_train_distractors 0 --nbr_test_distractors 0 \
--obverter_use_decision_head \
--agent_loss_type NLL \
--metric_fast --metric_epoch_period 5 \
--nb_3dshapespybullet_shapes 5 --nb_3dshapespybullet_colors 8 \
--nb_3dshapespybullet_samples 100 --nb_3dshapespybullet_train_colors 7 \
--lr 1e-3

#--egocentric

#--emb_dropout_prob 0.5 --dropout_prob 0.0 --use_sentences_one_hot_vectors \

#--parent_folder /home/kevin/debugging_RG/TestNewObverter/August19/\
#
# --force_eos
#--resizeDim 32 --arch BN+3xCNN3x3
#--resizeDim 64 --arch BN+BaselineCNN


# TODO: investigate 2 output decision heads / linear decision heads followed by log softmax at nll loss computation.