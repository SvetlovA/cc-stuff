"""Equal AI partners on one repository -- the code behind `partner.py`.

Layers, lowest first; a module only imports from layers below it:

    timing, util             clocks and small helpers with no project knowledge
    state                    .partner/ paths, roster.json, wrapper scripts
    transcript               chat.md: appending, parsing, cursors, debts
    prompts                  every text an agent is briefed or messaged with
    providers/               agent CLIs: finding, probing, launching them
    terminals/               terminal tabs: finding, opening, typing into them
    presence                 who is alive and who is listening right now
    waking                   typing a wake-up into a tab that stopped looping
    briefing, sessions       seed.md for each agent; archiving and relaunching
    idle                     pausing the loops once every agent has finished
    commands/                one function per subcommand
    cli                      the argument parser that routes to them
"""
