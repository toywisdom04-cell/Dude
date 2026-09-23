import ast

# Test the isolated function
source = '''def get_observation_learner(
    perception_service = None,
    perception_engine = None,
    memory = None,
    procedure_store = None,
    procedure_learner = None,
    intelligence_router = None,
    enable_learning = False,
) -> "ObservationLearner":
    """Factory to create an ObservationLearner instance."""
    return ObservationLearner(
        perception_service = None,
        perception_engine = None,
        memory = None,
        procedure_store = None,
        procedure_learner = None,
        intelligence_router = None,
        enable_learning = False,
    )'''

try:
    ast.parse(source)
    print('Isolated function parses OK')
except SyntaxError as e:
    print(f'SyntaxError: {e}')
    print(f'Error at line {e.lineno}, offset {e.offset}')