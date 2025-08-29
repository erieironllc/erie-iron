Your task is to extract the stack trace for the exception that caused the execution to fail. 
- Return the stack trace as a single string including newline characters. 
- Additionally, include the minimum number of preceding log lines needed to provide context. 

The response should be a plain unstructured string (a snippet of the log)

**you must** include any AWS role or permissions WARNING or ERROR lines (and surrounding context) if they exist
- these lines may include the string "is not authorized to perform"