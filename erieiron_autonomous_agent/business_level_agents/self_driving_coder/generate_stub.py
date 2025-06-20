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
            stub_lines.append("    raise NotImplementedError()")

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
