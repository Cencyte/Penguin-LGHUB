import java.io.RandomAccessFile;
import java.nio.channels.FileLock;

/** A live JVM with unsaved heap state, random-access storage and a POSIX file lock. */
public class LiveFileMoveFixture {
    public static void main(String[] args) throws Exception {
        try (RandomAccessFile file = new RandomAccessFile(args[0], "rw");
             FileLock lock = file.getChannel().lock()) {
            long counter = 0;
            while (true) {
                file.seek(0);
                file.writeLong(++counter);
                if (counter % 100 == 0) System.out.println(args[1] + " counter=" + counter);
                Thread.sleep(10);
            }
        }
    }
}
