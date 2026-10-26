// Save a small analyzed test program for ordinary/packed export equivalence checks.
// @category DragonShard.Tests
import ghidra.app.script.GhidraScript;
import ghidra.framework.data.DefaultProjectData;
import ghidra.framework.model.*;
import ghidra.program.model.listing.Program;
import java.io.File;

public class DragonShardPackFixture extends GhidraScript {
    public void run() throws Exception {
        String[] args = getScriptArgs();
        DefaultProjectData project = new DefaultProjectData(new ProjectLocator(args[0], args[1]), false, false);
        Program program = null;
        try {
            DomainFile file = project.getFile(args[2]);
            program = (Program)file.getReadOnlyDomainObject(this, DomainFile.DEFAULT_VERSION, monitor);
            program.saveToPackedFile(new File(args[3]), monitor);
        } finally {
            if (program != null) program.release(this);
            project.close();
        }
    }
}
