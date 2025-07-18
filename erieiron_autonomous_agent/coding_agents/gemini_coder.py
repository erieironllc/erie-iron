import subprocess


def generate_code_with_gemini(prompt: str) -> str:
    """
    Generates code using the gemini-cli.

    Args:
        prompt: The prompt to use for code generation.

    Returns:
        The generated code.
    """
    try:
        result = subprocess.run(
            ["gemini-cli", prompt],
            capture_output=True,
            text=True,
            check=True,
        )
        return result.stdout
    except FileNotFoundError:
        return "Error: gemini-cli not found. Please ensure it is installed and in your PATH."
    except subprocess.CalledProcessError as e:
        return f"Error: gemini-cli returned a non-zero exit code: {e.stderr}"
