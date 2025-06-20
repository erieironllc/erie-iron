Additional Important Policies.  All policies must be followed:
  - the main code file MUST save the ensemble checkpoints to a directory named <artifacts_directory>
        - The checkpoint files need to have all of the data required to use the model at a later point for inference
  - the main code file must expose a method named "def infer(obj)" on the <execute_module> - ie it should expose "<execute_module>.infer(obj)"
        - the infer(obj) method accepts a single item from the test dataset, and returns the inferred value or values
        - the infer(obj) method must load a model from the checkpoints in <artifacts_directory> (or ensemble checkpoints in <artifacts_directory>).  This is necessary to validate the model can be reconstructed from the checkpoint files
        - the final test evaluation must use this infer() method when scoring the performance of the model
        - if you want to use the infer(obj) method in the train or test steps, that is ok but not necessary
  - the code must plot learing rate and loss curve diagrams and same them to the directory <artifacts_directory>