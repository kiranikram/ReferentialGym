from .module import Module

from .flatten_module import build_FlattenModule, FlattenModule 
from .concat_module import build_ConcatModule, ConcatModule 
from .squeeze_module import build_SqueezeModule, SqueezeModule 
from .batch_reshape_module import build_BatchReshapeModule, BatchReshapeModule 
from .batch_reshape_repeat_module import build_BatchReshapeRepeatModule, BatchReshapeRepeatModule 

from .multi_head_classification_module import build_MultiHeadClassificationModule, MultiHeadClassificationModule
from .multi_head_classification_from_feature_map_module import build_MultiHeadClassificationFromFeatureMapModule, MultiHeadClassificationFromFeatureMapModule
from .multi_head_regression_module import build_MultiHeadRegressionModule, MultiHeadRegressionModule

from .homoscedastic_multi_task_loss_module import build_HomoscedasticMultiTasksLossModule, HomoscedasticMultiTasksLossModule
from .optimization_module import build_OptimizationModule, OptimizationModule 

from .current_agent_module import CurrentAgentModule 
from .population_handler_module import build_PopulationHandlerModule, PopulationHandlerModule 

from .visual_module import build_VisualModule, VisualModule

from .per_epoch_logger_module import build_PerEpochLoggerModule, PerEpochLoggerModule
from .grad_recorder_module import build_GradRecorderModule, GradRecorderModule
from .topographic_similarity_metric_module import build_TopographicSimilarityMetricModule, TopographicSimilarityMetricModule
from .factor_vae_disentanglement_metric_module import build_FactorVAEDisentanglementMetricModule, FactorVAEDisentanglementMetricModule