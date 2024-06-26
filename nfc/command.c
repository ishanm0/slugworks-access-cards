#include <stdlib.h>
#include <stdint.h>
#include <stdio.h>
#include <string.h>

// CRCs by byte (from 0x00 to 0xFF)
static const uint16_t CCITTCRCTable[256] = {
    0x0000, 0x1021, 0x2042, 0x3063, 0x4084, 0x50a5,
    0x60c6, 0x70e7, 0x8108, 0x9129, 0xa14a, 0xb16b,
    0xc18c, 0xd1ad, 0xe1ce, 0xf1ef, 0x1231, 0x0210,
    0x3273, 0x2252, 0x52b5, 0x4294, 0x72f7, 0x62d6,
    0x9339, 0x8318, 0xb37b, 0xa35a, 0xd3bd, 0xc39c,
    0xf3ff, 0xe3de, 0x2462, 0x3443, 0x0420, 0x1401,
    0x64e6, 0x74c7, 0x44a4, 0x5485, 0xa56a, 0xb54b,
    0x8528, 0x9509, 0xe5ee, 0xf5cf, 0xc5ac, 0xd58d,
    0x3653, 0x2672, 0x1611, 0x0630, 0x76d7, 0x66f6,
    0x5695, 0x46b4, 0xb75b, 0xa77a, 0x9719, 0x8738,
    0xf7df, 0xe7fe, 0xd79d, 0xc7bc, 0x48c4, 0x58e5,
    0x6886, 0x78a7, 0x0840, 0x1861, 0x2802, 0x3823,
    0xc9cc, 0xd9ed, 0xe98e, 0xf9af, 0x8948, 0x9969,
    0xa90a, 0xb92b, 0x5af5, 0x4ad4, 0x7ab7, 0x6a96,
    0x1a71, 0x0a50, 0x3a33, 0x2a12, 0xdbfd, 0xcbdc,
    0xfbbf, 0xeb9e, 0x9b79, 0x8b58, 0xbb3b, 0xab1a,
    0x6ca6, 0x7c87, 0x4ce4, 0x5cc5, 0x2c22, 0x3c03,
    0x0c60, 0x1c41, 0xedae, 0xfd8f, 0xcdec, 0xddcd,
    0xad2a, 0xbd0b, 0x8d68, 0x9d49, 0x7e97, 0x6eb6,
    0x5ed5, 0x4ef4, 0x3e13, 0x2e32, 0x1e51, 0x0e70,
    0xff9f, 0xefbe, 0xdfdd, 0xcffc, 0xbf1b, 0xaf3a,
    0x9f59, 0x8f78, 0x9188, 0x81a9, 0xb1ca, 0xa1eb,
    0xd10c, 0xc12d, 0xf14e, 0xe16f, 0x1080, 0x00a1,
    0x30c2, 0x20e3, 0x5004, 0x4025, 0x7046, 0x6067,
    0x83b9, 0x9398, 0xa3fb, 0xb3da, 0xc33d, 0xd31c,
    0xe37f, 0xf35e, 0x02b1, 0x1290, 0x22f3, 0x32d2,
    0x4235, 0x5214, 0x6277, 0x7256, 0xb5ea, 0xa5cb,
    0x95a8, 0x8589, 0xf56e, 0xe54f, 0xd52c, 0xc50d,
    0x34e2, 0x24c3, 0x14a0, 0x0481, 0x7466, 0x6447,
    0x5424, 0x4405, 0xa7db, 0xb7fa, 0x8799, 0x97b8,
    0xe75f, 0xf77e, 0xc71d, 0xd73c, 0x26d3, 0x36f2,
    0x0691, 0x16b0, 0x6657, 0x7676, 0x4615, 0x5634,
    0xd94c, 0xc96d, 0xf90e, 0xe92f, 0x99c8, 0x89e9,
    0xb98a, 0xa9ab, 0x5844, 0x4865, 0x7806, 0x6827,
    0x18c0, 0x08e1, 0x3882, 0x28a3, 0xcb7d, 0xdb5c,
    0xeb3f, 0xfb1e, 0x8bf9, 0x9bd8, 0xabbb, 0xbb9a,
    0x4a75, 0x5a54, 0x6a37, 0x7a16, 0x0af1, 0x1ad0,
    0x2ab3, 0x3a92, 0xfd2e, 0xed0f, 0xdd6c, 0xcd4d,
    0xbdaa, 0xad8b, 0x9de8, 0x8dc9, 0x7c26, 0x6c07,
    0x5c64, 0x4c45, 0x3ca2, 0x2c83, 0x1ce0, 0x0cc1,
    0xef1f, 0xff3e, 0xcf5d, 0xdf7c, 0xaf9b, 0xbfba,
    0x8fd9, 0x9ff8, 0x6e17, 0x7e36, 0x4e55, 0x5e74,
    0x2e93, 0x3eb2, 0x0ed1, 0x1ef0};

// copied from B1 user manual
static uint16_t GetCCITTCRC(const uint8_t *Data, uint32_t Size)
{
    uint16_t CRC;
    uint16_t Temp;
    uint32_t Index;
    if (Size == 0)
    {
        return 0;
    }
    CRC = 0xFFFF;
    for (Index = 0; Index < Size; Index++)
    {
        Temp = (uint16_t)((CRC >> 8) ^ Data[Index]) & 0x00FF;
        CRC = CCITTCRCTable[Temp] ^ (CRC << 8);
    }
    return CRC;
}

void print_command(uint8_t *data, uint16_t len)
{
    // calculate the CRC for the data
    uint16_t data_crc = GetCCITTCRC(data, len);

    // calculate the length of the data and crc together
    uint16_t data_crc_len = len + 2;

    // create the header for the command - 02 indicates the start of any message, followed by the length of the data and data crc
    uint8_t header[] = {0x02, data_crc_len & 0xFF, (data_crc_len >> 8) & 0xFF};

    // calculate the CRC for the header
    uint16_t header_crc = GetCCITTCRC(header, sizeof(header));

    // print the header
    for (int i = 0; i < sizeof(header); i++)
    {
        printf("%02X ", header[i]);
    }
    // print the header CRC
    printf("%02X %02X ", header_crc & 0xFF, (header_crc >> 8) & 0xFF);

    // print the data
    for (int i = 0; i < len; i++)
    {
        printf("%02X ", data[i]);
    }
    // print the data CRC
    printf("%02X %02X\n", data_crc & 0xFF, (data_crc >> 8) & 0xFF);
}

int main(int argc, char *argv[])
{
    /* printf("Dummy:\n");
    // send 0x00 as uart dummy command - just to get an ack response
    uint8_t dummy_data[] = {0x00};
    print_command(dummy_data, sizeof(dummy_data));

    printf("\nRead UID into Memory:\n");
    // 0x01 for uart write, 0x0001 for command register address (flipped to 01 00 for LSB first), 0x0001 for 1 byte data (flipped to 01 00 for LSB first), 0x01 for RFID "Get UID and Type" camera
    uint8_t uid_data[] = {0x01, 0x01, 0x00, 0x01, 0x00, 0x01};
    print_command(uid_data, sizeof(uid_data));

    printf("\nOutput UID from Memory:\n");
    // 0x02 for uart read, 0x0014 for Tag UID register address (flipped to 14 00 for LSB first), 0x000A for 10 bytes of data (flipped to 0A 00 for LSB first)
    uint8_t uid_reg_data[] = {0x02, 0x14, 0x00, 0x0A, 0x00};
    print_command(uid_reg_data, sizeof(uid_reg_data)); */

    if (argc < 2)
    {
        printf("Usage: %s <command> <data>\n", argv[0]);
        return 1;
    }
    else
    {
        // printf("\nCustom Command:\n");
        uint8_t custom_data[argc - 1];
        for (int i = 1; i < argc; i++)
        {
            custom_data[i - 1] = strtol(argv[i], NULL, 16);
            // printf("custom_data[%d] = %02x\n", i-1, custom_data[i-1]);
        }
        print_command(custom_data, sizeof(custom_data));
    }
    // for (int i = 1; i < argc; i++)
    // {
    //     printf("%s ", argv[i]);
    // }
    // printf("\n");

    return 0;
}
