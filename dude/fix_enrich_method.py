with open(r'E:\Dude\dude\core\orchestrator\goal_executor.py', 'r', encoding='utf-8') as f:
    lines = f.readlines()

# The _enrich_interpretation method is nested inside _translate_routing_result
# Need to extract it and place it as a proper class method

new_lines = []
i = 0
in_translate = False
in_enrich = False
enrich_body = []
translate_depth = 0

while i < len(lines):
    line = lines[i]
    
    if 'def _translate_routing_result' in line:
        in_translate = True
        new_lines.append(line)
        i += 1
        continue
    
    if 'def _enrich_interpretation' in line:
        # Skip the nested _enrich_interpretation definition, we'll add it at class level
        # Skip until we see the return statement of the nested method
        while i < len(lines):
            if lines[i].strip() == 'return interpretation':
                i += 1
                break
            i += 1
        continue
    
    # Check if we're at the end of _translate_routing_result method
    if line.strip() == 'return interpretation' and 'def _enrich_interpretation' in ''.join(lines[max(0,i-30):i]):
        i += 1
        continue
    
    # Check if we're at the next method after _translate_routing_result
    if line.strip().startswith('def ') and '_translate_routing_result' not in line and '_translate' not in line and 'def _' in line:
        # We've reached the next method after _translate_routing_result
        # Insert _enrich_interpretation here before this method
        new_lines.append('\n')
        new_lines.append('    def _enrich_interpretation(\n')
        new_lines.append('        self, \n')
        new_lines.append('        interpretation: CognitiveInterpretation, \n')
        new_lines.append('        context: Dict[str, Any]\n')
        new_lines.append('    ) -> CognitiveInterpretation:\n')
        new_lines.append('        """Add execution plan or perception focus based on mode."""\n')
        new_lines.append('\n')
        new_lines.append('        if interpretation.intent_kind == CognitiveMode.COMPUTER_WORK:\n')
        new_lines.append('            # Get execution plan from IntelligenceRouter\n')
        new_lines.append('            if self.intelligence_router and context["active_goal"]:\n')
        new_lines.append('                try:\n')
        new_lines.append('                    routing = self.intelligence_router.route(\n')
        new_lines.append('                        task_state=context["active_goal"],\n')
        new_lines.append('                        perception=context["perception"],\n')
        new_lines.append('                        user_intent=context["transcript"],\n')
        new_lines.append('                    )\n')
        new_lines.append('                    interpretation.execution_plan = routing.plan\n')
        new_lines.append('                    interpretation.intent_kind = CognitiveMode.COMPUTER_WORK\n')
        new_lines.append('                except Exception as e:\n')
        new_lines.append('                    log.warning(f"Planning failed: {e}")\n')
        new_lines.append('\n')
        new_lines.append('        elif interpretation.intent_kind == CognitiveMode.ANSWER_FROM_LIVE_ENV:\n')
        new_lines.append('            interpretation.perception_focus = "full_screen_with_ocr"\n')
        new_lines.append('\n')
        new_lines.append('        elif interpretation.intent_kind == CognitiveMode.MEMORY_OPERATION:\n')
        new_lines.append('            interpretation.required_capabilities = ["episodic_memory", "fact_memory"]\n')
        new_lines.append('\n')
        new_lines.append('        return interpretation\n')
        new_lines.append('\n')
        new_lines.append(line)
        i += 1
        continue
    
    new_lines.append(line)
    i += 1

with open(r'E:\Dude\dude\core\orchestrator\goal_executor.py', 'w', encoding='utf-8') as f:
    f.writelines(new_lines)
print('Fixed')