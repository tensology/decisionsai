(function() {
    window.ProjectsTabSections = window.ProjectsTabSections || {};
    window.ProjectsTabSections.terminal = {
        onActivated: function() {
            setTimeout(function() {
                var input = document.getElementById("shell-terminal-command-input");
                if (input) input.focus();
            }, 0);
        }
    };
})();
