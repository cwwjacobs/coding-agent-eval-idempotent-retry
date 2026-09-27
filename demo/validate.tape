# Renders demo/validate.gif from a real run of the validation matrix.
# Re-render from the repository root:  vhs demo/validate.tape
Output demo/validate.gif

Set Shell "bash"
Set FontSize 14
Set Width 1060
Set Height 390
Set Padding 22
Set Theme "Catppuccin Mocha"
Set TypingSpeed 40ms

Hide
Type "export PS1='$ ' && clear"
Enter
Show

Sleep 500ms
Type "python3 harness/validate.py"
Sleep 400ms
Enter
Sleep 16s
