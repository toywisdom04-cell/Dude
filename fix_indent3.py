with open(r'E:\Dude\dude\core\orchestrator\goal_executor.py', 'r', encoding='utf-8') as f:
    content = f.read()

# Fix the indentation of _translate_routing_result method
old = '''        def _translate_routing_result(self, routing_result, context: Dict[str, Any]) -> CognitiveInterpretation:
        """Translate IntelligenceRouter RoutingResult to CognitiveInterpretation."""
        decision = routing_result.decision
        
        # Map routing decisions to cognitive modes
        if decision == RouteDecision.DETERMINISTIC_SKILL:'''

new = '''        def _translate_routing_result(self, routing_result, context: Dict[str, Any]) -> CognitiveInterpretation:
        """Translate IntelligenceRouter RoutingResult to CognitiveInterpretation."""
        decision = routing_result.decision
        
        # Map routing decisions to cognitive modes
        if decision == RouteDecision.DETERMINISTIC_SKILL:'''

content = content.replace(old, new)

with open(r'E:\Dude\dude\core\orchestrator\goal_executor.py', 'w', encoding='utf-8') as f:
    f.write(content)
print('Fixed')