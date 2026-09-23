"""DUDE Orchestrator Package.

Architectural foundation for the DUDE task runtime.
Provides structured state, perception, and action abstractions.
"""

from .state import (
    TaskState,
    SubGoal,
    StepResult,
    Action,
    TargetSpec,
    ExpectedResult,
    ActualResult,
    VerificationMethod,
    VerificationResult,
    GroundingMethod,
    RiskLevel,
    TaskType,
    OrchestratorState,
    VoiceState,
    InterruptRelation,
    UnexpectedState,
    PermissionState,
    MemoryBundle,
    Procedure,
    RecoveryDecision,
    Plan,
    ProcedureParameter,
    ProcedureComposition,
)

from .perception import (
    PerceptionSnapshot,
    PerceptionCache,
    PerceptionEngine,
    PerceptionLevel,
    ControlInfo,
    OCRRegion,
    ScreenDelta,
    Rect,
)

from .intelligence import (
    IntelligenceBackend,
    ModelCapabilities,
    HealthStatus,
    GenerateRequest,
    GenerateResponse,
    StreamRequest,
    IntelligenceBackendRegistry,
    get_registry,
    register_backend,
    get_backend,
)

from .skills import (
    Skill,
    SkillRegistry,
    SkillExecutor,
    get_skill_registry,
    register_skill,
)

from .procedure_store import (
    ProcedureStore,
    ProcedureCandidate,
    get_procedure_store,
    create_procedure_from_trace,
)
from .pattern_store import (
    PatternStore,
    PersistedPattern,
    get_pattern_store,
)
from .procedure_learner import (
    ProcedureLearner,
    get_procedure_learner,
)
from .procedure_adaptation import (
    ProcedureAdaptation,
    ProcedureAdaptationStore,
    ProcedureAdaptationManager,
    get_adaptation_store,
    get_adaptation_manager,
)
from .procedure_composition import (
    ProcedureCompositionStore,
    ProcedureCompositionManager,
    get_composition_store,
    get_composition_manager,
)
from .state import ProcedureComposition

from .intelligence_router import (
    IntelligenceRouter,
    RouteDecision,
    RoutingResult,
    create_intelligence_router,
    get_intelligence_router,
)

from .observation_learner import (
    ObservationLearner,
    get_observation_learner,
    PatternDetector,
    WorkflowCandidateBuilder,
    ObservedPattern,
    ObservedWorkflow,
)
from .observation_log import ObservationLog
from .perceptual_memory import (
    PerceptualMemory,
    PerceptualMemoryPolicy,
    LayerDecision,
)
from .perception_service import (
    PerceptionService,
    SemanticObservation,
    PerceptionLevel,
    ChangeClass,
    ObservationClass,
    LiveSceneState,
    ObservationLog as PerceptionObservationLog,
    FrameRing,
    LatencyMetrics,
)

from .intelligence_router import (
    IntelligenceRouter,
    RouteDecision,
    RoutingResult,
    create_intelligence_router,
    get_intelligence_router,
)

from .task_engine import TaskEngine
from .action_executor import ActionExecutor, ExecutionStatus
from .verification import VerificationEngine
from .recovery import RecoveryEngine
from .recovery_action_executor import RecoveryActionExecutor, get_recovery_action_executor
from .brain_adapter import BrainAdapter, create_brain_adapter
from .parameter_extractor import (
    get_parameter_extractor,
    ParameterExtractor,
    ParameterPattern,
    ExtractionResult,
    ExtractedParameter,
)
from .parameter_binder import (
    ParameterBinder,
    BindingResult,
)

__all__ = [
    # State
    "TaskState",
    "SubGoal",
    "StepResult",
    "Action",
    "TargetSpec",
    "ExpectedResult",
    "ActualResult",
    "VerificationMethod",
    "VerificationResult",
    "GroundingMethod",
    "RiskLevel",
    "TaskType",
    "OrchestratorState",
    "VoiceState",
    "InterruptRelation",
    "UnexpectedState",
    "PermissionState",
    "MemoryBundle",
    "Procedure",
    "ProcedureParameter",
    "RecoveryDecision",
    "Plan",
    # Perception
    "PerceptionSnapshot",
    "PerceptionCache",
    "PerceptionEngine",
    "PerceptionLevel",
    "ControlInfo",
    "OCRRegion",
    "ScreenDelta",
    "Rect",
    # Intelligence
    "IntelligenceBackend",
    "ModelCapabilities",
    "HealthStatus",
    "GenerateRequest",
    "GenerateResponse",
    "StreamRequest",
    "IntelligenceBackendRegistry",
    "get_registry",
    "register_backend",
    "get_backend",
    # Phase 2B: Intelligence Router & Skills
    "Skill",
    "SkillRegistry",
    "SkillExecutor",
    "get_skill_registry",
    "register_skill",
    "ProcedureStore",
    "ProcedureCandidate",
    "get_procedure_store",
    "create_procedure_from_trace",
    # Phase 9E: Observation Learning
    "PatternStore",
    "PersistedPattern",
    "get_pattern_store",
    "ObservedPattern",
    "ObservedWorkflow",
    "PatternDetector",
    "WorkflowCandidateBuilder",
    "ObservationLearner",
    "get_observation_learner",
    "PerceptualMemory",
    "PerceptualMemoryPolicy",
    "LayerDecision",
    "IntelligenceRouter",
    "RouteDecision",
    "RoutingResult",
    "create_intelligence_router",
    "get_intelligence_router",
    # Phase 2D: Procedure Binding
    "ParameterExtractor",
    "ParameterPattern",
    "ExtractionResult",
    "ExtractedParameter",
    "get_parameter_extractor",
    "ParameterBinder",
    "BindingResult",
    # Phase 2F: Procedure Adaptation & Composition
    "ProcedureAdaptation",
    "ProcedureComposition",
    "ProcedureAdaptationStore",
    "ProcedureAdaptationManager",
    "ProcedureCompositionStore",
    "ProcedureCompositionManager",
    "get_adaptation_store",
    "get_adaptation_manager",
    "get_composition_store",
    "get_composition_manager",
    # Core components
    "TaskEngine",
    "ActionExecutor",
    "ExecutionStatus",
    "VerificationEngine",
    "RecoveryEngine",
    "RecoveryActionExecutor",
    "get_recovery_action_executor",
    "BrainAdapter",
    "create_brain_adapter",
]