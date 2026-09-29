package cmd

import (
	"encoding/json"
	"slices"
	"strings"
	"testing"
)

func TestGmailModifyLabelFlags(t *testing.T) {
	commands := []struct {
		name string
		args []string
		op   string
	}{
		{"thread", []string{"gmail", "thread", "modify", "t1"}, "gmail.thread.modify"},
		{"batch", []string{"gmail", "batch", "modify", "m1"}, "gmail.batch.modify"},
		{"labels", []string{"gmail", "labels", "modify", "t1"}, "gmail.labels.modify"},
		{"message", []string{"gmail", "messages", "modify", "m1"}, "gmail.messages.modify"},
	}
	scenarios := []struct {
		name      string
		flags     []string
		add       []string
		remove    []string
		wantUsage bool
	}{
		{
			name:  "repeated flags",
			flags: []string{"--add=AAA", "--add=BBB", "--remove=XXX", "--remove=YYY"},
			add:   []string{"AAA", "BBB"}, remove: []string{"XXX", "YYY"},
		},
		{
			name:  "comma-separated flags",
			flags: []string{"--add=AAA,BBB", "--remove=XXX,YYY"},
			add:   []string{"AAA", "BBB"}, remove: []string{"XXX", "YYY"},
		},
		{
			name:  "trim whitespace",
			flags: []string{"--add= INBOX , STARRED ", "--remove= UNREAD , TRASH "},
			add:   []string{"INBOX", "STARRED"}, remove: []string{"UNREAD", "TRASH"},
		},
		{name: "blank add", flags: []string{"--add=,"}, wantUsage: true},
		{name: "blank remove", flags: []string{"--remove= , "}, wantUsage: true},
		{
			name:  "literal backslashes before commas",
			flags: []string{"--add=Projects\\,STARRED", "--remove=Tags\\,UNREAD"},
			add:   []string{"Projects\\", "STARRED"}, remove: []string{"Tags\\", "UNREAD"},
		},
		{
			name: "mixed repeats and commas",
			flags: []string{
				"--add=AAA,BBB", "--add=Projects\\,STARRED", "--add=CCC",
				"--remove=XXX", "--remove=Tags\\,UNREAD", "--remove=YYY,ZZZ",
			},
			add:    []string{"AAA", "BBB", "Projects\\", "STARRED", "CCC"},
			remove: []string{"XXX", "Tags\\", "UNREAD", "YYY", "ZZZ"},
		},
		{
			name:  "ignore empty occurrences",
			flags: []string{"--add=,", "--add=INBOX", "--remove=,"},
			add:   []string{"INBOX"},
		},
	}

	for _, command := range commands {
		for _, scenario := range scenarios {
			t.Run(command.name+"/"+scenario.name, func(t *testing.T) {
				args := append([]string{"--json", "--dry-run"}, command.args...)
				args = append(args, scenario.flags...)
				result := executeWithTestRuntime(t, args, nil)
				if scenario.wantUsage {
					if result.err == nil || ExitCode(result.err) != 2 ||
						!strings.Contains(result.err.Error(), "must specify --add and/or --remove") {
						t.Fatalf("expected usage error, got %v; stdout=%q", result.err, result.stdout)
					}
					return
				}
				if result.err != nil {
					t.Fatalf("dry-run: %v\n%s", result.err, result.stderr)
				}
				var got struct {
					Op      string
					Request struct {
						Add    []string
						Remove []string
					}
				}
				if err := json.Unmarshal([]byte(result.stdout), &got); err != nil {
					t.Fatalf("decode dry-run: %v\nstdout=%q", err, result.stdout)
				}
				if got.Op != command.op || !slices.Equal(got.Request.Add, scenario.add) ||
					!slices.Equal(got.Request.Remove, scenario.remove) {
					t.Fatalf("got op=%q add=%q remove=%q; want op=%q add=%q remove=%q",
						got.Op, got.Request.Add, got.Request.Remove, command.op, scenario.add, scenario.remove)
				}
			})
		}
	}
}
