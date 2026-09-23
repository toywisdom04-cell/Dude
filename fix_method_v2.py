with open(r'E:\Dude\dude\core\orchestrator\observation_learner.py', 'rb') as f:
    content = f.read()

# Find the _extract_parameters method
idx = content.find(b'def _extract_parameters')
if idx >= 0:
    # Find the end of the method (next def or class)
    next_def = content.find(b'\ndef ', idx + 1)
    next_class = content.find(b'\nclass ', idx + 1)
    method_end = len(content)
    if next_def != -1 and (next_class == -1 or next_def < next_class):
        method_end = next_def
    elif next_class != -1:
        method_end = next_class
    else:
        method_end = len(content)
    
    # The current broken method
    old_method = content[idx:method_end]
    
    # New properly formatted method
    new_method = b'''    def _extract_parameters(self, pattern: ObservedPattern) -> list[ProcedureParameter]:
        """Extract parameter definitions from pattern contexts using ParameterExtractor."""
        params = []
        param_names_seen = set()
        
        # 1. Use ParameterExtractor on the goal text for parameters from natural language
        goal_text = self._generate_goal(pattern)
        
        if self._extractor:
            extraction = self._extractor.extract(goal_text, pattern.parameters)
            for param_name, value in extraction.parameters.items():
                if isinstance(value, str) and value and param_name not in param_names_seen:
                    param_type = "string"
                    if value.isdigit():
                        param_type = "integer"
                    params.append(ProcedureParameter(
                        name=param_name,
                        type=param_type,
                        required=True,
                        description=f"Parameter extracted from observation goal: {param_name}",
                        default=value,
                    ))
                    param_names_seen.add(param_name)
        
        # 2. Extract parameters from window titles in contexts using ParameterExtractor
        # This catches filenames, paths, etc. from window titles like "report_A.txt - Notepad"
        for ctx in pattern.contexts:
            window_title = ctx.get("window", "")
            if window_title:
                extraction = self._extractor.extract(window_title, [])
                for param_name, value in extraction.parameters.items():
                    if isinstance(value, str) and value and param_name not in param_names_seen:
                        param_type = "string"
                        if value.isdigit():
                            param_type = "integer"
                        # Check if this looks like a filename/path
                        if param_name in ("filename", "path", "file_path", "folder_name"):
                            param_type = "path" if "/" in value or "\\\\" in value else "filename"
                        params.append(ProcedureParameter(
                            name=param_name,
                            type=param_type,
                            required=True,
                            description=f"Parameter extracted from window title: {param_name}",
                            default=value,
                        ))
                        param_names_seen.add(param_name)
        
        # Also look for variable parts in window titles (fallback for prefix/suffix)
        titles = [ctx.get("window", "") for ctx in pattern.contexts]
        if titles:
            common_prefix = self._common_prefix(titles)
            common_suffix = self._common_suffix(titles)
            if common_prefix and common_prefix != titles[0] and "window_title_prefix" not in param_names_seen:
                params.append(ProcedureParameter(
                    name="window_title_prefix",
                    type="string",
                    required=False,
                    description="Variable prefix in window title",
                    default=common_prefix,
                ))
            if common_suffix and common_suffix != titles[0] and "window_title_suffix" not in param_names_seen:
                params.append(ProcedureParameter(
                    name="window_title_suffix",
                    type="string",
                    required=False,
                    description="Variable suffix in window title",
                    default=common_suffix,
                ))
        return params'''

    if old_method in content:
        new_content = content.replace(old_method, new_method)
        with open(r'E:\Dude\dude\core\orchestrator\observation_learner.py', 'wb') as f:
            f.write(new_content)
        print('Successfully replaced _extract_parameters method')
    else:
        print('Old method not found - content mismatch')
        print('Expected prefix:', old_method[:200])
        print('Actual prefix:', content.find(b'def _extract_parameters'))