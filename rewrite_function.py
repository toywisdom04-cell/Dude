with open(r'E:\Dude\dude\core\orchestrator\observation_learner.py', 'rb') as f:
    content = f.read()

# Find the get_observation_learner function and rewrite it completely
old_func = b'''def get_observation_learner(
    perception_service: Optional[PerceptionService] = None,
    perception_engine: Optional[PerceptionEngine] = None,
    memory: Optional[Memory] = None,
    procedure_store: Optional[ProcedureStore] = None,
    procedure_learner: Optional[ProcedureLearner] = None,
    intelligence_router: Optional[IntelligenceRouter] = None,
    enable_learning: bool = False,
) -> ObservationLearner:
    """Factory to create an ObservationLearner instance."""
    return ObservationLearner(
        perception_service=perception_service,
        perception_engine=perception_engine,
        memory=memory,
        procedure_store=procedure_store,
        procedure_learner=procedure_learner,
        intelligence_router=intelligence_router,
        enable_learning=enable_learning,
    )'''

new_func = b'''def get_observation_learner(
    perception_service: Optional[PerceptionService] = None,
    perception_engine: Optional[PerceptionEngine] = None,
    memory: Optional[Memory] = None,
    procedure_store: Optional[ProcedureStore] = None,
    procedure_learner: Optional[ProcedureLearner] = None,
    intelligence_router: Optional[IntelligenceRouter] = None,
    enable_learning: bool = False,
) -> ObservationLearner:
    """Factory to create an ObservationLearner instance."""
    return ObservationLearner(
        perception_service=perception_service,
        perception_engine=perception_engine,
        memory=memory,
        procedure_store=procedure_store,
        procedure_learner=procedure_learner,
        intelligence_router=intelligence_router,
        enable_learning=enable_learning,
    )'''

if old in content:
    content = content.replace(old, new)
    with open(r'E:\Dude\dude\core\orchestrator\observation_learner.py', 'wb') as f:
        f.write(content)
    print('Rewrote function')
else:
    print('Function not found')