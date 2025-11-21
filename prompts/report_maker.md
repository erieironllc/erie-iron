# Report Generation Prompt

## INPUTS
### TITLE
[Your report title here]

### GOAL (Internal - Do Not State Explicitly)
[The objective this report should support]

### DATA
[Insert JSON and/or plain text data here]

---

## INSTRUCTIONS

Generate a professional, human-readable report with the following requirements:

1. **Use the TITLE as the report's main heading**

2. **Analyze the provided DATA to support the GOAL** without explicitly mentioning the goal itself

3. **Report Structure:**
   - Executive summary or key findings
   - Main body with logical sections based on data patterns
   - Relevant insights, trends, or observations
   - Conclusions drawn from the data

4. **Presentation Guidelines:**
   - Write in clear, accessible prose
   - Use headings, subheadings, bullet points, or numbered lists as appropriate
   - Highlight significant patterns, outliers, or correlations in the data
   - Present findings that naturally lead the reader toward the goal's conclusion
   - Maintain professional, objective tone

5. **What NOT to include:**
   - No explicit statement of the goal
   - No meta-commentary about the report's purpose
   - No raw data dumps without interpretation

# Output Format
**You must**
- Format the report in **markdown** 
- Optimize formatting for human readability and human comprehension
- **never** return structured data like json - you must format using markdown ready for human reading

Generate the report now.  
