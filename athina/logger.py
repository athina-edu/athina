class Logger:
    verbose = False
    print_debug_messages = False
    log_file = ""

    def vprint(self, string, debug=False):
        if self.verbose:
            print(string)
        else:
            # Log to a file
            try:
                if (self.print_debug_messages is True and debug is True) or (debug is False):
                    with open(self.log_file, "a") as file_desc:
                        file_desc.write("%s\n" % string)
            except (AttributeError, PermissionError, FileNotFoundError):  # If LOG_FILE is not set
                print(string)
