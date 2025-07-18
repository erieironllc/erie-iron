import ast
import os


def generate_stub(input_file: str, stub_file: str):
    """
    Generates a stub Python file containing only function
    signatures and docstrings (with NotImplementedError bodies)
    from the given input Python module.
    """
    with open(input_file, 'r') as f:
        source = f.read()

    tree = ast.parse(source, filename=input_file)
    stub_lines = []

    # Preserve module-level imports and simple assignments like __version__
    for node in tree.body:
        if isinstance(node, ast.Assign):
            targets = [t.id for t in node.targets if isinstance(t, ast.Name)]
            if targets:
                line = source.splitlines()[node.lineno - 1]
                stub_lines.append(line)
        elif isinstance(node, (ast.Import, ast.ImportFrom)):
            line = source.splitlines()[node.lineno - 1]
            stub_lines.append(line)

    # Extract function definitions
    for node in tree.body:
        if isinstance(node, ast.FunctionDef):
            # Build signature
            args = []
            for arg in node.args.args:
                arg_str = arg.arg
                if arg.annotation:
                    arg_str += f": {ast.unparse(arg.annotation)}"
                args.append(arg_str)
            # varargs and kwargs
            if node.args.vararg:
                args.append(f"*{node.args.vararg.arg}")
            if node.args.kwarg:
                args.append(f"**{node.args.kwarg.arg}")
            # Defaults
            defaults = [ast.unparse(d) for d in node.args.defaults]
            num_defaults = len(defaults)
            if num_defaults:
                for i in range(-1, -num_defaults - 1, -1):
                    args[i] += f"={defaults[i]}"
            signature = f"def {node.name}({', '.join(args)})"
            if node.returns:
                signature += f" -> {ast.unparse(node.returns)}"
            signature += ":"
            stub_lines.append("")
            stub_lines.append(signature)

            # Docstring
            doc = ast.get_docstring(node)
            if doc:
                stub_lines.append(f'    """{doc}"""')

            # Placeholder body
            stub_lines.append("    pass")
        elif isinstance(node, ast.ClassDef):
            # Check if class inherits from Exception
            if any(base for base in node.bases if isinstance(base, ast.Name) and "Exception" in base.id):
                stub_lines.append("")
                stub_lines.append(f"class {node.name}({', '.join([ast.unparse(base) for base in node.bases])}):")
                doc = ast.get_docstring(node)
                if doc:
                    stub_lines.append(f'    """{doc}"""')

                # Look for an __init__ method and extract signature
                init_func = next((n for n in node.body if isinstance(n, ast.FunctionDef) and n.name == "__init__"), None)
                if init_func:
                    args = []
                    for arg in init_func.args.args:
                        arg_str = arg.arg
                        if arg.annotation:
                            arg_str += f": {ast.unparse(arg.annotation)}"
                        args.append(arg_str)
                    if init_func.args.vararg:
                        args.append(f"*{init_func.args.vararg.arg}")
                    if init_func.args.kwarg:
                        args.append(f"**{init_func.args.kwarg.arg}")
                    defaults = [ast.unparse(d) for d in init_func.args.defaults]
                    num_defaults = len(defaults)
                    if num_defaults:
                        for i in range(-1, -num_defaults - 1, -1):
                            args[i] += f"={defaults[i]}"
                    signature = f"    def __init__({', '.join(args)}):"
                    stub_lines.append(signature)
                    stub_lines.append("        pass")
                else:
                    stub_lines.append("    pass")

    # Write to stub file
    os.makedirs(os.path.dirname(stub_file), exist_ok=True)
    with open(stub_file, 'w') as f:
        f.write("\n".join(stub_lines))


# If run as a script, demonstrate usage
if __name__ == "__main__":
    import sys

    if len(sys.argv) != 3:
        print("Usage: python generate_stub.py <input_file> <stub_file>")
    else:
        generate_stub(sys.argv[1], sys.argv[2])
        print(f"Stub generated at {sys.argv[2]}")
