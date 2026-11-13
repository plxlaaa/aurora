package version

import (
	"fmt"
	"runtime"
)

var (
	Version = "0.5.1"
	Commit  = "dev"
	Date    = "unknown"
)

func String() string {
	return fmt.Sprintf("%s (%s %s/%s, commit %s, built %s)",
		Version, runtime.Version(), runtime.GOOS, runtime.GOARCH, Commit, Date)
}

func Short() string {
	return Version
}

func Platform() string {
	return runtime.GOOS + "-" + runtime.GOARCH
}

func AssetName() string {
	return "aurora-" + Platform() + ".zip"
}
