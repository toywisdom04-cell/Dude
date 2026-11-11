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
from .visual_state import (
    VisualState,
    VisualStateBus,
    describe_snapshot,
    get_visual_state_bus,
)
from .spatial_workspace import (
    SpatialTarget,
    SpatialOverlay,
    SpatialWorkspace,
    get_workspace,
    resolve_named,
    resolve_focus,
)
from .spatial_hand_interaction import (
    SpatialObject,
    SpatialHandState,
    SpatialSemanticEvent,
    SpatialInteractionLayer,
)
from .hand_tracking import (
    Gesture,
    SemanticInteraction,
    HandObservation,
    HandState,
    HandStateBus,
    HandStabilizer,
    HandInteraction,
    HandInteractionMapper,
    HandActionBridge,
    HandCamera,
    HandPerception,
    HandScreenMapper,
    HandTracker,
    get_hand_tracker,
    relate_to_visual,
)
from .skill_packs import (
    SkillPack,
    SkillPackRegistry,
    get_skill_pack_registry,
    rank_packs,
)
from .context_primer import (
    ContextBundle,
    ContextPrimer,
    get_context_primer,
    last_context_primer,
)
from .shared_workspace import (
    ModalityEvidence,
    SharedWorkspace,
    compose_workspace,
    publish_workspace,
    last_workspace,
)
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
    # Phase 14: Visual State Bus
    "VisualState",
    "VisualStateBus",
    "describe_snapshot",
    "get_visual_state_bus",
    # Phase 15: Spatial Workspace
    "SpatialTarget",
    "SpatialOverlay",
    "SpatialWorkspace",
    "get_workspace",
    "resolve_named",
    "resolve_focus",
    # Phase 16: Hand Tracking
    "Gesture",
    "SemanticInteraction",
    "HandObservation",
    "HandState",
    "HandStateBus",
    "HandStabilizer",
    "HandInteraction",
    "HandInteractionMapper",
    "HandActionBridge",
    "HandCamera",
    "HandPerception",
    "HandScreenMapper",
    "HandTracker",
    "get_hand_tracker",
    "relate_to_visual",
    "SpatialObject",
    "SpatialHandState",
    "SpatialSemanticEvent",
    "SpatialInteractionLayer",
    # Phase 17: Skill Packs + Context Priming
    "SkillPack",
    "SkillPackRegistry",
    "get_skill_pack_registry",
    "rank_packs",
    "ContextBundle",
    "ContextPrimer",
    "get_context_primer",
    "last_context_primer",
    # Phase 19: Multimodal Shared Workspace
    "ModalityEvidence",
    "SharedWorkspace",
    "compose_workspace",
    "publish_workspace",
    "last_workspace",
    "ActionExecutor",
    "ExecutionStatus",
    "VerificationEngine",
    "RecoveryEngine",
    "RecoveryActionExecutor",
    "get_recovery_action_executor",
    "BrainAdapter",
    "create_brain_adapter",
]