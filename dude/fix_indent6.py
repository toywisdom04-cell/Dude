with open(r'E:\Dude\dude\core\orchestrator\goal_executor.py', 'r', encoding='utf-8') as f:
    content = f.read()

# Fix the indentation issues in _enrich_interpretation method
old = '''            if interpretation.intent_kind == CognitiveMode.COMPUTER_WORK:
            # Get execution plan from IntelligenceRouter
            if self.intelligence_router and context["active_goal"]:
                try:
                    routing = self.intelligence_router.route(
                        task_state=context["active_goal"],
                        perception=context["perception"],
                        user_intent=context["transcript"],
                    )
                    interpretation.execution_plan = routing.plan
                    interpretation.intent_kind = CognitiveMode.COMPUTER_WORK
                except Exception as e:
                    log.warning(f"Planning failed: {e}")
            
            elif interpretation.intent_kind == CognitiveMode.ANSWER_FROM_LIVE_ENV:
            interpretation.perception_focus = "full_screen_with_ocr"
            
            elif interpretation.intent_kind == CognitiveMode.MEMORY_OPERATION:
            interpretation.required_capabilities = ["episodic_memory", "fact_memory"]
            
            return interpretation'''

new = '''            if interpretation.intent_kind == CognitiveMode.COMPUTER_WORK:
                # Get execution plan from IntelligenceRouter
                if self.intelligence_router and context["active_goal"]:
                    try:
                        routing = self.intelligence_router.route(
                            task_state=context["active_goal"],
                            perception=context["perception"],
                            user_intent=context["transcript"],
                        )
                        interpretation.execution_plan = routing.plan
                        interpretation.intent_kind = CognitiveMode.COMPUTER_WORK
                    except Exception as e:
                        log.warning(f"Planning failed: {e}")
            
            elif interpretation.intent_kind == CognitiveMode.ANSWER_FROM_LIVE_ENV:
                interpretation.perception_focus = "full_screen_with_ocr"
            
            elif interpretation.intent_kind == CognitiveMode.MEMORY_OPERATION:
                interpretation.required_capabilities = ["episodic_memory", "fact_memory"]
            
            return interpretation'''

content = content.replace(old, new)

with open(r'E:\Dude\dude\core\orchestrator\goal_executor.py', 'w', encoding='utf-8') as f:
    f.write(content)
print('Fixed')